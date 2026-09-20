"""
Traffic Classification Service

Classifies CDR records into traffic types and extracts analysis dimensions
for regulatory reporting and rating.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from functools import lru_cache

from reference.models import MccMnc, ImsiPrefix, NumberingPlan, TrunkGroup, Operator

logger = logging.getLogger(__name__)


# Sierra Leone MCC/MNC mappings
SIERRA_LEONE_MCC = '619'
OPERATOR_MNCS = {
    'orange': '01',
    'africell': '03',
    'qcell': '04',
    'sierratel': '05',
}


# Cache reference data at module level (shared across instances)
_NUMBERING_PLAN_CACHE = {}
_IMSI_PREFIX_CACHE = {}
_MCC_MNC_CACHE = {}
_CACHE_LOADED = False


def _load_reference_caches():
    """Load reference data into memory caches."""
    global _CACHE_LOADED, _NUMBERING_PLAN_CACHE, _IMSI_PREFIX_CACHE, _MCC_MNC_CACHE

    if _CACHE_LOADED:
        return

    try:
        # Load numbering plan (prefix -> country/operator mapping)
        numbering_plans = list(
            NumberingPlan.objects.filter(enabled=True)
            .values_list('prefix', 'country', 'operator')[:2000]
        )
        _NUMBERING_PLAN_CACHE = {
            prefix: {'country': country, 'operator': operator}
            for prefix, country, operator in numbering_plans
        }

        # Load IMSI prefixes (prefix -> operator mapping)
        imsi_prefixes = list(
            ImsiPrefix.objects.all()
            .values_list('prefix', 'operator')[:1000]
        )
        _IMSI_PREFIX_CACHE = {
            prefix: operator for prefix, operator in imsi_prefixes
        }

        # Load MCC/MNC (dial_code -> country mapping)
        mcc_mncs = list(
            MccMnc.objects.all()
            .values_list('dial_code', 'country')[:500]
        )
        _MCC_MNC_CACHE = {
            dial_code: country for dial_code, country in mcc_mncs
        }

        _CACHE_LOADED = True
        logger.info(
            f"Loaded reference caches: {len(_NUMBERING_PLAN_CACHE)} numbering plans, "
            f"{len(_IMSI_PREFIX_CACHE)} IMSI prefixes, {len(_MCC_MNC_CACHE)} MCC/MNCs"
        )
    except Exception as e:
        logger.error(f"Failed to load reference caches: {e}")


class TrafficClassifier:
    """Classifies CDR records into traffic types and extracts dimensions."""

    def __init__(self):
        """Initialize classifier with cached reference data."""
        # Load caches on first instantiation
        if not _CACHE_LOADED:
            _load_reference_caches()

    def classify(self, record: Any, operator_code: str) -> Dict[str, Any]:
        """
        Classify a single CDR record.

        Args:
            record: CDR record (unsaved model instance or dict)
            operator_code: The operator code for the CDR file

        Returns:
            Classification dict with traffic types and dimensions
        """
        # Extract raw data from record
        raw_data = self._extract_raw_data(record)

        # Extract key fields
        calling_no = self._get_field(raw_data, ['CALLING_NO', 'CALLING_PARTY_NUMBER', 'calling_number'])
        called_no = self._get_field(raw_data, ['CALLED_NO', 'CALLED_PARTY_NUMBER', 'called_number'])
        imsi = self._get_field(raw_data, ['IMSI_A', 'IMSI', 'imsi'])
        service_type = self._get_field(raw_data, ['SERVICE_TYPE', 'service_type', 'TELESERVICE_CODE'])
        rat_type = self._get_field(raw_data, ['RAT_TYPE', 'rat_type', 'NETWORK_TECHNOLOGY'])
        call_duration = self._get_field(raw_data, ['CALL_DURATION', 'duration', 'CHARGEABLE_DURATION'])
        network_element = self._get_field(raw_data, ['MSC_ID', 'msc_id', 'NETWORK_ENTITY', 'network_element', 'SWITCH_IDENTITY'])
        call_direction = self._get_field(raw_data, ['CALL_DIRECTION', 'call_direction', 'SERVICE_USAGE_DIRECTION'])

        # Normalize service type
        service_type = self._normalize_service_type(service_type)

        # Determine traffic types
        traffic_types = self._determine_traffic_types(
            calling_no, called_no, imsi, operator_code, service_type
        )

        # Extract analysis dimensions
        dimensions = {
            'operator_code': operator_code,
            'service_type': service_type,
            'network_technology': self._normalize_technology(rat_type),
            'network_element': network_element or 'UNKNOWN',
            'traffic_direction': self._normalize_direction(call_direction),
            'traffic_type': traffic_types.get('primary', 'UNKNOWN'),
            'destination_country': traffic_types.get('destination_country'),
            'destination_operator': traffic_types.get('destination_operator'),
            'subscriber_category': self._extract_subscriber_category(raw_data),
            'source_stream': self._determine_source_stream(record),
        }

        # Combine traffic types and dimensions
        classification = {
            'traffic_types': traffic_types,
            'dimensions': dimensions,
            'raw_fields': {
                'calling_no': calling_no,
                'called_no': called_no,
                'imsi': imsi,
                'service_type': service_type,
                'duration': call_duration,
            }
        }

        return classification

    def _extract_raw_data(self, record: Any) -> Dict[str, Any]:
        """Extract raw data from record (model instance or dict).

        For model instances the MSC/IMS/PGW processors strip key columns
        from ``raw_data`` to save space (they live on the model instead).
        We merge model-level fields back so the classifier can read them
        regardless of which dict they ended up in.
        """
        if isinstance(record, dict):
            return record

        raw = {}

        # 1. Start with model-instance fields (lowercase Django columns)
        if hasattr(record, '_meta'):
            for field in record._meta.get_fields():
                if not hasattr(field, 'name'):
                    continue
                name = field.name
                if name in ('id', 'file', 'raw_data', 'created_at', 'status',
                            'source', 'paired_record'):
                    continue
                value = getattr(record, name, None)
                if value is not None and str(value) != '':
                    raw[name] = value

        # 2. Overlay raw_data (may contain extra decoder fields)
        if hasattr(record, 'raw_data') and record.raw_data:
            raw.update(record.raw_data)

        return raw

    def _get_field(self, raw_data: Dict, field_names: List[str]) -> Optional[str]:
        """Get field value from raw data, trying multiple possible field names."""
        for field_name in field_names:
            value = raw_data.get(field_name)
            if value:
                return str(value).strip()
        return None

    def _normalize_service_type(self, service_type: Optional[str]) -> str:
        """Normalize service type to standard values."""
        if not service_type:
            return 'UNKNOWN'

        service_type = str(service_type).upper().strip()

        # Voice indicators (includes CALLFORWARDING which is a voice service)
        if any(x in service_type for x in ['VOICE', 'TELEPHONY', 'SPEECH', 'MOC',
                                             'MTC', 'TELE', 'FORWARDING', 'CALL']):
            return 'VOICE'

        # SMS indicators
        if any(x in service_type for x in ['SMS', 'SHORT', 'MESSAGE']):
            return 'SMS'

        # Data indicators
        if any(x in service_type for x in ['DATA', 'GPRS', 'PDP', 'INTERNET', 'BEARER']):
            return 'DATA'

        return 'UNKNOWN'

    def _normalize_technology(self, rat_type: Optional[str]) -> str:
        """Normalize network technology to 2G/3G/4G."""
        if not rat_type:
            return 'UNKNOWN'

        rat_type = str(rat_type).upper()

        if '4G' in rat_type or 'LTE' in rat_type or 'VOLTE' in rat_type:
            return '4G'
        if '3G' in rat_type or 'UMTS' in rat_type or 'HSPA' in rat_type:
            return '3G'
        if '2G' in rat_type or 'GSM' in rat_type or 'GPRS' in rat_type or 'EDGE' in rat_type:
            return '2G'

        return 'UNKNOWN'

    def _normalize_direction(self, call_direction: Optional[str]) -> str:
        """Normalize call direction."""
        if not call_direction:
            return 'BOTH'

        direction = str(call_direction).upper()

        if 'ORIGIN' in direction or 'MO' in direction or 'OUT' in direction:
            return 'ORIGINATING'
        if 'TERM' in direction or 'MT' in direction or 'IN' in direction:
            return 'TERMINATING'

        return 'BOTH'

    def _determine_traffic_types(self, calling_no: Optional[str],
                                  called_no: Optional[str],
                                  imsi: Optional[str],
                                  operator_code: str,
                                  service_type: str) -> Dict[str, Any]:
        """
        Determine traffic types for the record.

        Returns dict with:
        - primary: main traffic type (ON_NET, OFF_NET, INTERNATIONAL, ROAMING, INTERCONNECT)
        - destination_country: for international traffic
        - destination_operator: for interconnect
        - is_roaming: boolean
        - is_international: boolean
        """
        traffic_types = {
            'VOICE': service_type == 'VOICE',
            'SMS': service_type == 'SMS',
            'DATA': service_type == 'DATA',
            'INTERNATIONAL': False,
            'ROAMING': False,
            'INTERCONNECT': False,
            'ON_NET': False,
            'OFF_NET': False,
            'INBOUND': False,
            'OUTBOUND': False,
            'primary': 'UNKNOWN',
            'destination_country': None,
            'destination_operator': None,
        }

        # Determine if roaming (IMSI not from home network)
        is_roaming = self._is_roaming(imsi, operator_code)
        traffic_types['ROAMING'] = is_roaming

        # Determine if international (called number outside Sierra Leone)
        dest_country = self._get_destination_country(called_no)
        is_international = dest_country and dest_country != 'Sierra Leone'
        traffic_types['INTERNATIONAL'] = is_international
        traffic_types['destination_country'] = dest_country

        # Determine destination operator
        dest_operator = self._get_destination_operator(called_no, imsi)
        traffic_types['destination_operator'] = dest_operator

        # Determine if interconnect (different operator)
        is_interconnect = dest_operator and dest_operator.lower() != operator_code.lower()
        traffic_types['INTERCONNECT'] = is_interconnect

        # Determine on-net vs off-net
        if not is_international and not is_roaming:
            if is_interconnect:
                traffic_types['OFF_NET'] = True
            elif dest_operator and dest_operator.lower() == operator_code.lower():
                traffic_types['ON_NET'] = True
            else:
                traffic_types['OFF_NET'] = True  # Default to off-net if unknown

        # Determine primary traffic type
        if is_international:
            traffic_types['primary'] = 'INTERNATIONAL'
        elif is_roaming:
            traffic_types['primary'] = 'ROAMING'
        elif is_interconnect:
            traffic_types['primary'] = 'INTERCONNECT'
        elif traffic_types['ON_NET']:
            traffic_types['primary'] = 'ON_NET'
        else:
            traffic_types['primary'] = 'OFF_NET'

        return traffic_types

    def _is_roaming(self, imsi: Optional[str], operator_code: str) -> bool:
        """Check if IMSI indicates roaming (not from home operator).

        Sierra Leone (MCC 619) uses 2-digit MNCs, so IMSI layout is
        MCC(3) + MNC(2) + MSIN(10).  We match the MNC length from
        OPERATOR_MNCS so this also works if a 3-digit MNC is added later.
        """
        if not imsi:
            return False

        imsi = str(imsi).strip()
        if not imsi.startswith(SIERRA_LEONE_MCC):
            return True

        home_mnc = OPERATOR_MNCS.get(operator_code.lower())
        if not home_mnc:
            return False

        mnc_len = len(home_mnc)
        if len(imsi) >= 3 + mnc_len:
            imsi_mnc = imsi[3:3 + mnc_len]
            return imsi_mnc != home_mnc

        return False

    def _get_destination_country(self, called_no: Optional[str]) -> Optional[str]:
        """Get destination country from called number using numbering plan cache."""
        if not called_no:
            return None

        called_no = str(called_no).strip()

        # Remove leading + if present
        if called_no.startswith('+'):
            called_no = called_no[1:]

        # Check if international (not Sierra Leone code 232)
        if called_no.startswith('232'):
            return 'Sierra Leone'

        # Try to match against numbering plan cache
        for prefix, data in _NUMBERING_PLAN_CACHE.items():
            if called_no.startswith(prefix):
                return data.get('country')

        # If not in numbering plan, check MCC/MNC cache for international
        if len(called_no) >= 3 and called_no[:3] != '232':
            dial_code = '+' + called_no[:3]
            return _MCC_MNC_CACHE.get(dial_code)

        return None

    def _get_destination_operator(self, called_no: Optional[str], imsi: Optional[str]) -> Optional[str]:
        """Get destination operator from called number or IMSI prefix cache."""
        # Try IMSI prefix cache first
        if imsi and len(imsi) >= 6:
            imsi_prefix = imsi[:6]
            operator = _IMSI_PREFIX_CACHE.get(imsi_prefix)
            if operator:
                return operator

        # Try numbering plan cache for called number
        if called_no:
            called_no = str(called_no).strip()
            if called_no.startswith('+'):
                called_no = called_no[1:]

            # Find longest matching prefix
            for i in range(min(6, len(called_no)), 0, -1):
                prefix = called_no[:i]
                data = _NUMBERING_PLAN_CACHE.get(prefix)
                if data:
                    return data.get('operator')

        return None

    def _extract_subscriber_category(self, raw_data: Dict) -> str:
        """Extract subscriber category (prepaid/postpaid)."""
        for field in ['SUBSCRIBER_CATEGORY', 'PREPAID_FLAG', 'subscriber_category', 'prepaid_flag']:
            value = raw_data.get(field)
            if value is None:
                continue
            value = str(value).strip().upper()
            if not value or value == '0':
                continue
            if value == '1' or 'PREPAID' in value or value == 'P':
                return 'PREPAID'
            if 'POSTPAID' in value or 'POST' in value or value == 'C':
                return 'POSTPAID'

        return ''

    def _determine_source_stream(self, record: Any) -> str:
        """Determine source stream from record type."""
        # Check model class name
        if hasattr(record, '__class__'):
            class_name = record.__class__.__name__.upper()
            if 'MSC' in class_name:
                return 'MSC'
            if 'IMS' in class_name:
                return 'IMS'
            if 'PGW' in class_name:
                return 'PGW'
            if 'SGSN' in class_name:
                return 'SGSN'
            if 'SGW' in class_name:
                return 'SGW'
            if 'CBS' in class_name:
                return 'CBS'

        # Try to get from raw data
        raw_data = self._extract_raw_data(record)
        network_element = self._get_field(raw_data, ['NETWORK_ENTITY', 'MSC_ID', 'network_element'])
        if network_element:
            network_element = network_element.upper()
            if 'MSC' in network_element:
                return 'MSC'
            if 'IMS' in network_element:
                return 'IMS'
            if 'PGW' in network_element:
                return 'PGW'
            if 'SGSN' in network_element:
                return 'SGSN'
            if 'SGW' in network_element:
                return 'SGW'
            if 'CBS' in network_element:
                return 'CBS'

        return 'UNKNOWN'


def classify_record(record: Any, operator_code: str) -> Dict[str, Any]:
    """Convenience function to classify a single record."""
    classifier = TrafficClassifier()
    return classifier.classify(record, operator_code)


def classify_batch(records: List[Any], operator_code: str) -> List[Dict[str, Any]]:
    """Classify a batch of records."""
    classifier = TrafficClassifier()
    classifications = []
    for record in records:
        try:
            classification = classifier.classify(record, operator_code)
            classifications.append(classification)
        except Exception as e:
            logger.error(f"Failed to classify record: {e}")
            classifications.append(None)
    return classifications


def clear_reference_caches():
    """Clear reference data caches (useful for testing or manual refresh)."""
    global _CACHE_LOADED, _NUMBERING_PLAN_CACHE, _IMSI_PREFIX_CACHE, _MCC_MNC_CACHE
    _CACHE_LOADED = False
    _NUMBERING_PLAN_CACHE.clear()
    _IMSI_PREFIX_CACHE.clear()
    _MCC_MNC_CACHE.clear()
    logger.info("Reference caches cleared")
