from django import forms

from reference.models import Operator

import re

from .models import BenchmarkCampaign, Cell, Chiefdom, FrequencyBand, Region, RegulatoryRule, RegulatoryThreshold, Sector, Site
from .services.analysis import _METRIC_EXTRACTORS, _OPS
from .services.rules import CONDITION_LABELS

# Condition/metric choices are read directly from the analysis engine's own supported
# vocabulary (services/analysis.py) rather than duplicated here, so a rule can never be
# saved with a condition or metric the engine would silently ignore.
CONDITION_CHOICES = [('', 'Select condition')] + [
    (k, CONDITION_LABELS.get(k, k)) for k in _OPS if k.isalpha()
]
METRIC_CHOICES = [('', 'Select metric')] + [
    (k, k.replace('_', ' ').upper()) for k in _METRIC_EXTRACTORS
]
# Rule/threshold applicability is forward-looking configuration (a rule can be prepared
# ahead of a technology's rollout), so this offers the fixed 3GPP generation set already
# used elsewhere in this app (e.g. session_list's technology filter) rather than only
# technologies observed in measurements so far.
RULE_TECHNOLOGY_CHOICES = [('', 'All Technologies')] + [(t, t) for t in ('2G', '3G', '4G', '5G')]


class _ChiefdomChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.district.region.name} › {obj.district.name} › {obj.name}'


class SiteForm(forms.ModelForm):
    """Add / edit a network site. Region and district are implied by the chosen chiefdom,
    so a site can never reference an inconsistent geography."""

    chiefdom = _ChiefdomChoice(
        queryset=Chiefdom.objects.none(), required=False, empty_label='— Not specified —')

    class Meta:
        model = Site
        fields = ['site_id', 'name', 'operator', 'site_type', 'chiefdom', 'address',
                  'latitude', 'longitude', 'altitude_m', 'is_active']
        labels = {'site_id': 'Site Code', 'name': 'Site Name', 'altitude_m': 'Altitude (m)',
                  'is_active': 'Active'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['chiefdom'].queryset = (
            Chiefdom.objects.select_related('district__region')
            .order_by('district__region__name', 'district__name', 'name'))
        self.fields['operator'].queryset = Operator.objects.filter(enabled=True).order_by('name')
        self.fields['operator'].empty_label = 'Select operator'
        if self.instance.pk:
            # The operator owns this site's sectors and cells; moving it would corrupt lineage.
            self.fields['operator'].disabled = True
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'dt-sl-input')
        for name in ('latitude', 'longitude'):
            self.fields[name].widget.attrs['step'] = 'any'

    def clean_site_id(self):
        return self.cleaned_data['site_id'].strip()

    def clean_name(self):
        return self.cleaned_data['name'].strip()

    def clean_latitude(self):
        v = self.cleaned_data.get('latitude')
        if v is not None and not -90 <= v <= 90:
            raise forms.ValidationError('Latitude must be between -90 and 90.')
        return v

    def clean_longitude(self):
        v = self.cleaned_data.get('longitude')
        if v is not None and not -180 <= v <= 180:
            raise forms.ValidationError('Longitude must be between -180 and 180.')
        return v

    def clean(self):
        data = super().clean()
        lat, lon = data.get('latitude'), data.get('longitude')
        if (lat is None) != (lon is None) and 'latitude' not in self.errors and 'longitude' not in self.errors:
            raise forms.ValidationError('Provide both latitude and longitude, or leave both empty.')
        if lat == 0 and lon == 0:
            raise forms.ValidationError('0, 0 is not a valid site location. Leave coordinates empty if unknown.')
        return data


# (min, max) per identifier; PCI is 0-503 on LTE and 0-1007 on NR.
_ID_RANGES = {
    'lac': (0, 65535), 'rac': (0, 255), 'tac': (0, 16777215), 'ci': (0, 268435455),
    'eci': (0, 268435455), 'nci': (0, 68719476735), 'earfcn': (0, 262143), 'nrarfcn': (0, 3279165),
}
_LABELS = {'lac': 'LAC', 'rac': 'RAC', 'tac': 'TAC', 'ci': 'CI', 'eci': 'ECI', 'nci': 'NCI',
           'pci': 'PCI', 'earfcn': 'EARFCN', 'nrarfcn': 'NR-ARFCN'}


class CellForm(forms.ModelForm):
    """Add / edit a cell. The parent site is chosen by code (scoped to the operator) and the
    sector by code, so the form never has to list thousands of sites. Unknown sites are
    rejected, never created; an unknown sector code is created under the given site, matching
    the reference importer."""

    site_code = forms.CharField(max_length=50, label='Site Code')
    sector_code = forms.CharField(max_length=50, label='Sector')

    class Meta:
        model = Cell
        fields = ['cell_id', 'operator', 'technology', 'mcc', 'mnc', 'lac', 'rac', 'tac', 'ci', 'eci',
                  'nci', 'pci', 'earfcn', 'nrarfcn', 'band', 'latitude', 'longitude', 'altitude_m', 'is_active']
        labels = {'cell_id': 'Cell Code', 'mcc': 'MCC', 'mnc': 'MNC', 'lac': 'LAC', 'rac': 'RAC', 'tac': 'TAC',
                  'ci': 'CI', 'eci': 'ECI', 'nci': 'NCI', 'pci': 'PCI', 'earfcn': 'EARFCN',
                  'nrarfcn': 'NR-ARFCN', 'band': 'Frequency Band', 'altitude_m': 'Altitude (m)',
                  'is_active': 'Active'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operator'].queryset = Operator.objects.filter(enabled=True).order_by('name')
        self.fields['operator'].empty_label = 'Select operator'
        self.fields['technology'].choices = [('', 'Select technology')] + list(Cell.Technology.choices)
        self.fields['band'].queryset = FrequencyBand.objects.order_by('technology', 'band_number')
        self.fields['band'].empty_label = '— Not specified —'
        if self.instance.pk:
            self.fields['operator'].disabled = True     # the operator owns this cell's history
            self.fields['site_code'].initial = self.instance.sector.site.site_id
            self.fields['sector_code'].initial = self.instance.sector.sector_id
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'dt-sl-input')
        for name in ('latitude', 'longitude'):
            self.fields[name].widget.attrs['step'] = 'any'

    def clean_cell_id(self):
        return self.cleaned_data['cell_id'].strip()

    def clean_mcc(self):
        v = self.cleaned_data.get('mcc', '').strip()
        if v and not re.fullmatch(r'\d{3}', v):
            raise forms.ValidationError('MCC must be exactly 3 digits.')
        return v

    def clean_mnc(self):
        v = self.cleaned_data.get('mnc', '').strip()
        if v and not re.fullmatch(r'\d{2,3}', v):
            raise forms.ValidationError('MNC must be 2 or 3 digits.')
        return v

    def clean_latitude(self):
        v = self.cleaned_data.get('latitude')
        if v is not None and not -90 <= v <= 90:
            raise forms.ValidationError('Latitude must be between -90 and 90.')
        return v

    def clean_longitude(self):
        v = self.cleaned_data.get('longitude')
        if v is not None and not -180 <= v <= 180:
            raise forms.ValidationError('Longitude must be between -180 and 180.')
        return v

    def clean(self):
        data = super().clean()
        for name, (lo, hi) in _ID_RANGES.items():
            v = data.get(name)
            if v is not None and not lo <= v <= hi and name not in self.errors:
                self.add_error(name, f'{_LABELS[name]} must be between {lo} and {hi}.')
        tech, pci = data.get('technology'), data.get('pci')
        if pci is not None and 'pci' not in self.errors:
            top = 1007 if tech == '5G' else 503
            if not 0 <= pci <= top:
                self.add_error('pci', f'PCI must be between 0 and {top}.')
        lat, lon = data.get('latitude'), data.get('longitude')
        if (lat is None) != (lon is None) and 'latitude' not in self.errors and 'longitude' not in self.errors:
            raise forms.ValidationError('Provide both latitude and longitude, or leave both empty.')
        if lat == 0 and lon == 0:
            raise forms.ValidationError('0, 0 is not a valid cell location. Leave coordinates empty if unknown.')
        band = data.get('band')
        if band and tech and band.technology != tech:
            self.add_error('band', f'This band is a {band.technology} band, but the cell is {tech}.')

        operator = data.get('operator') or (self.instance.operator if self.instance.pk else None)
        code = (data.get('site_code') or '').strip()
        if operator and code and 'site_code' not in self.errors:
            self._site = Site.objects.filter(operator=operator, site_id=code).first()
            if self._site is None:
                self.add_error('site_code', f'No site with code "{code}" exists for {operator.name}. Add the site first.')
        return data

    def save(self, commit=True):
        cell = super().save(commit=False)
        sector, _ = Sector.objects.get_or_create(site=self._site, sector_id=self.cleaned_data['sector_code'].strip())
        cell.sector = sector
        # Composite keys are recomputed by Cell.save(); clear first so a removed value cannot leave a stale key.
        cell.cgi = ''
        cell.ecgi = ''
        if commit:
            cell.save()
        return cell


class SectorForm(forms.ModelForm):
    """Add / edit a sector. The parent site is chosen by code within the chosen operator and must
    already exist (never created here). Uniqueness is (site, sector code), as in the model."""

    operator = forms.ModelChoiceField(queryset=Operator.objects.none(), label='Operator')
    site_code = forms.CharField(max_length=50, label='Site Code')

    class Meta:
        model = Sector
        fields = ['sector_id', 'azimuth_deg', 'height_m', 'tilt_deg', 'electrical_tilt_deg', 'is_active']
        labels = {'sector_id': 'Sector Code', 'azimuth_deg': 'Azimuth (°)', 'height_m': 'Antenna Height (m)',
                  'tilt_deg': 'Mechanical Tilt (°)', 'electrical_tilt_deg': 'Electrical Tilt (°)', 'is_active': 'Active'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operator'].queryset = Operator.objects.filter(enabled=True).order_by('name')
        self.fields['operator'].empty_label = 'Select operator'
        if self.instance.pk:
            self.fields['operator'].initial = self.instance.site.operator
            self.fields['operator'].disabled = True    # a sector never moves between operators
            self.fields['site_code'].initial = self.instance.site.site_id
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'dt-sl-input')
        for name in ('azimuth_deg', 'height_m', 'tilt_deg', 'electrical_tilt_deg'):
            self.fields[name].widget.attrs['step'] = 'any'

    def clean_sector_id(self):
        return self.cleaned_data['sector_id'].strip()

    def clean_azimuth_deg(self):
        v = self.cleaned_data.get('azimuth_deg')
        if v is not None and not 0 <= v < 360:
            raise forms.ValidationError('Azimuth must be from 0 up to (not including) 360 degrees.')
        return v

    def clean_height_m(self):
        v = self.cleaned_data.get('height_m')
        if v is not None and not 0 <= v <= 500:
            raise forms.ValidationError('Antenna height must be between 0 and 500 m.')
        return v

    def _clean_tilt(self, name):
        v = self.cleaned_data.get(name)
        if v is not None and not -30 <= v <= 30:
            raise forms.ValidationError('Tilt must be between -30 and 30 degrees.')
        return v

    def clean_tilt_deg(self):
        return self._clean_tilt('tilt_deg')

    def clean_electrical_tilt_deg(self):
        return self._clean_tilt('electrical_tilt_deg')

    def clean(self):
        data = super().clean()
        operator, code = data.get('operator'), (data.get('site_code') or '').strip()
        self._site = None
        if operator and code:
            self._site = Site.objects.filter(operator=operator, site_id=code).first()
            if self._site is None:
                self.add_error('site_code', f'No site with code "{code}" exists for {operator.name}. Add the site first.')
        sid = data.get('sector_id')
        if self._site and sid:
            clash = Sector.objects.filter(site=self._site, sector_id=sid)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error('sector_id', f'Sector "{sid}" already exists on site {self._site.site_id}.')
        return data

    def save(self, commit=True):
        sector = super().save(commit=False)
        sector.site = self._site
        if commit:
            sector.save()
        return sector


class RegulatoryRuleForm(forms.ModelForm):
    """Add / edit a RegulatoryRule — the same model drive_test.services.analysis.
    AnalysisEngine already evaluates. No new rules engine; this only edits its rows."""

    technology = forms.ChoiceField(choices=RULE_TECHNOLOGY_CHOICES, required=False, label='Technology')
    metric = forms.ChoiceField(choices=METRIC_CHOICES, label='Metric / KPI')
    condition = forms.ChoiceField(choices=CONDITION_CHOICES, label='Condition')

    class Meta:
        model = RegulatoryRule
        fields = ['rule_code', 'name', 'description', 'technology', 'service_type', 'metric',
                  'condition', 'regulatory_reference', 'authority', 'is_active',
                  'effective_from', 'effective_to']
        labels = {
            'rule_code': 'Rule Code', 'name': 'Rule Name', 'service_type': 'Service Type',
            'regulatory_reference': 'Regulatory Reference', 'is_active': 'Active',
            'effective_from': 'Effective From', 'effective_to': 'Effective To',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'effective_from': forms.DateInput(attrs={'type': 'date'}),
            'effective_to': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'dt-sl-input')

    def clean_rule_code(self):
        return self.cleaned_data['rule_code'].strip()

    def clean_name(self):
        return self.cleaned_data['name'].strip()

    def clean(self):
        data = super().clean()
        efrom, eto = data.get('effective_from'), data.get('effective_to')
        if efrom and eto and efrom > eto and 'effective_from' not in self.errors and 'effective_to' not in self.errors:
            raise forms.ValidationError('Effective From must be on or before Effective To.')
        return data


class _RuleChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.rule_code} — {obj.name}'


class RegulatoryThresholdForm(forms.ModelForm):
    """Add / edit a RegulatoryThreshold. `rule` supplies the metric/condition/technology
    scope; this form only edits the value, unit, operator applicability and effective
    window that already exist on the model — no geography field exists on this model,
    so none is shown here."""

    rule = _RuleChoice(queryset=RegulatoryRule.objects.order_by('name'), label='Regulatory Rule')

    class Meta:
        model = RegulatoryThreshold
        fields = ['rule', 'operator', 'warning_value', 'critical_value', 'unit',
                  'effective_from', 'effective_to']
        labels = {
            'warning_value': 'Warning Value', 'critical_value': 'Critical Value',
            'effective_from': 'Effective From', 'effective_to': 'Effective To',
        }
        widgets = {
            'effective_from': forms.DateInput(attrs={'type': 'date'}),
            'effective_to': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operator'].queryset = Operator.objects.filter(enabled=True).order_by('name')
        self.fields['operator'].empty_label = 'All Operators'
        self.fields['operator'].required = False
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'dt-sl-input')

    def clean_unit(self):
        return self.cleaned_data['unit'].strip()

    def clean(self):
        data = super().clean()
        efrom, eto = data.get('effective_from'), data.get('effective_to')
        if efrom and eto and efrom > eto and 'effective_from' not in self.errors and 'effective_to' not in self.errors:
            raise forms.ValidationError('Effective From must be on or before Effective To.')

        # Warning/critical ordering consistency, checked only against the chosen rule's
        # own condition — never against a hard-coded regulatory value.
        rule = data.get('rule')
        warning, critical = data.get('warning_value'), data.get('critical_value')
        if rule and warning is not None and critical is not None and 'warning_value' not in self.errors:
            if rule.condition in ('lt', 'le') and warning <= critical:
                self.add_error('warning_value',
                    'For a "less than" rule, the warning value must be greater than the critical value '
                    '(the metric crosses the warning line before the more severe critical line as it falls).')
            elif rule.condition in ('gt', 'ge') and warning >= critical:
                self.add_error('warning_value',
                    'For a "greater than" rule, the warning value must be less than the critical value '
                    '(the metric crosses the warning line before the more severe critical line as it rises).')
        return data


class BenchmarkCampaignForm(forms.ModelForm):
    class Meta:
        model = BenchmarkCampaign
        fields = ['name', 'description', 'region', 'date_from', 'date_to']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'date_from': forms.DateInput(attrs={'type': 'date'}),
            'date_to': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['region'].queryset = Region.objects.order_by('name')
        self.fields['region'].empty_label = 'All Regions'
        self.fields['region'].required = False
        for f in self.fields.values():
            if not isinstance(f.widget, (forms.CheckboxInput, forms.Textarea)):
                f.widget.attrs.setdefault('class', 'dt-sl-input')
            elif isinstance(f.widget, forms.Textarea):
                f.widget.attrs.setdefault('class', 'dt-sl-input')

    def clean(self):
        data = super().clean()
        d_from, d_to = data.get('date_from'), data.get('date_to')
        if d_from and d_to and d_from > d_to:
            raise forms.ValidationError('Start date must be on or before end date.')
        return data
