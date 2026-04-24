"""Shared enumerations for the mediation platform."""


class DecoderType:
    MSC = 'MSC'
    PGW = 'PGW'
    SGSN = 'SGSN'
    SGW = 'SGW'
    OCS = 'OCS'
    CBS = 'CBS'
    AUTO = 'AUTO'
    CSV = 'CSV'

    CHOICES = [
        (MSC, 'MSC (Huawei ASN.1/BER)'),
        (PGW, 'PGW (3GPP PS Domain)'),
        (SGSN, 'SGSN (3GPP PS Domain)'),
        (SGW, 'SGW (3GPP PS Domain)'),
        (OCS, 'OCS Input'),
        (CBS, 'CBS Output'),
        (AUTO, 'Auto-detect'),
        (CSV, 'Pre-decoded CSV'),
    ]


class ServiceType:
    VOICE = 'VOICE'
    SMS = 'SMS'
    DATA = 'DATA'

    CHOICES = [
        (VOICE, 'Voice'),
        (SMS, 'SMS'),
        (DATA, 'Data'),
    ]
