"""Seed initial Sierra Leone reference data."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from reference.models import MccMnc, ImsiPrefix, NumberingPlan, TrunkGroup

# ── MCC/MNC ──────────────────────────────────────────────
mcc_data = [
    ('619','01','Sierra Leone','Africell SL',True),
    ('619','02','Sierra Leone','Orange SL',True),
    ('619','03','Sierra Leone','Qcell SL',True),
    ('619','04','Sierra Leone','Sierratel',True),
    ('619','05','Sierra Leone','Africell SL B2',True),
    ('607','01','Guinea','Orange Guinée',False),
    ('607','02','Guinea','MTN Guinea',False),
    ('607','03','Guinea','Cellcom Guinée',False),
    ('614','01','Guinea-Bissau','MTN Guinea-Bissau',False),
    ('620','01','Ghana','MTN Ghana',False),
    ('620','02','Ghana','Vodafone Ghana',False),
    ('620','03','Ghana','AirtelTigo Ghana',False),
    ('621','01','Nigeria','MTN Nigeria',False),
    ('621','20','Nigeria','Airtel Nigeria',False),
    ('621','30','Nigeria','Glo Nigeria',False),
    ('621','50','Nigeria','9mobile Nigeria',False),
    ('605','01','Tunisia','Tunisie Telecom',False),
    ('206','01','Belgium','Proximus',False),
    ('234','10','UK','O2 UK',False),
    ('234','20','UK','Three UK',False),
    ('208','01','France','Orange France',False),
    ('232','01','Austria','A1 Telekom',False),
]
created = 0
for mcc, mnc, country, operator, is_home in mcc_data:
    obj, c = MccMnc.objects.update_or_create(
        mcc=mcc, mnc=mnc,
        defaults={'country': country, 'operator': operator, 'is_home': is_home, 'enabled': True}
    )
    if c:
        created += 1
print(f'MCC/MNC: {created} created, {MccMnc.objects.count()} total')

# ── IMSI Prefix ──────────────────────────────────────────
imsi_data = [
    ('61901','619','01','Africell SL','Sierra Leone',True),
    ('61902','619','02','Orange SL','Sierra Leone',True),
    ('61903','619','03','Qcell SL','Sierra Leone',True),
    ('61904','619','04','Sierratel','Sierra Leone',True),
    ('61905','619','05','Africell SL B2','Sierra Leone',True),
    ('60701','607','01','Orange Guinée','Guinea',False),
    ('60702','607','02','MTN Guinea','Guinea',False),
    ('62001','620','01','MTN Ghana','Ghana',False),
    ('62101','621','01','MTN Nigeria','Nigeria',False),
    ('62120','621','20','Airtel Nigeria','Nigeria',False),
    ('23410','234','10','O2 UK','United Kingdom',False),
    ('20801','208','01','Orange France','France',False),
]
created = 0
for prefix, mcc, mnc, operator, country, is_home in imsi_data:
    obj, c = ImsiPrefix.objects.update_or_create(
        prefix=prefix,
        defaults={'mcc': mcc, 'mnc': mnc, 'operator': operator, 'country': country, 'is_home': is_home, 'enabled': True}
    )
    if c:
        created += 1
print(f'IMSI Prefix: {created} created, {ImsiPrefix.objects.count()} total')

# ── Numbering Plan ───────────────────────────────────────
num_data = [
    ('23276','232','Sierra Leone','Africell SL','MOBILE',8,12),
    ('23277','232','Sierra Leone','Africell SL','MOBILE',8,12),
    ('23278','232','Sierra Leone','Orange SL','MOBILE',8,12),
    ('23279','232','Sierra Leone','Orange SL','MOBILE',8,12),
    ('23230','232','Sierra Leone','Qcell SL','MOBILE',8,12),
    ('23231','232','Sierra Leone','Qcell SL','MOBILE',8,12),
    ('23232','232','Sierra Leone','Sierratel','FIXED',8,12),
    ('23233','232','Sierra Leone','Sierratel','FIXED',8,12),
    ('23225','232','Sierra Leone','Africell SL','MOBILE',8,12),
    ('23234','232','Sierra Leone','Africell SL','MOBILE',8,12),
    ('112','232','Sierra Leone','Emergency Services','SHORTCODE',3,3),
    ('999','232','Sierra Leone','Emergency Services','SHORTCODE',3,3),
    ('117','232','Sierra Leone','Sierratel Directory','SHORTCODE',3,3),
    ('00','','International','International Access','INTERNATIONAL',4,15),
    ('22460','224','Guinea','Orange Guinée','MOBILE',9,12),
    ('22465','224','Guinea','MTN Guinea','MOBILE',9,12),
    ('2348','234','Nigeria','MTN Nigeria','MOBILE',11,14),
    ('2347','234','Nigeria','Airtel Nigeria','MOBILE',11,14),
    ('23324','233','Ghana','MTN Ghana','MOBILE',10,12),
    ('23320','233','Ghana','Vodafone Ghana','MOBILE',10,12),
]
created = 0
for prefix, cc, country, operator, ntype, minl, maxl in num_data:
    obj, c = NumberingPlan.objects.update_or_create(
        prefix=prefix,
        defaults={
            'country_code': cc, 'country': country, 'operator': operator,
            'number_type': ntype, 'min_length': minl, 'max_length': maxl, 'enabled': True
        }
    )
    if c:
        created += 1
print(f'Numbering Plan: {created} created, {NumberingPlan.objects.count()} total')

# ── Trunk Groups ─────────────────────────────────────────
trunk_data = [
    ('TG-SLT-IN','Sierratel Incoming','INTERCONNECT','INCOMING','Sierratel','Sierra Leone','23232'),
    ('TG-SLT-OUT','Sierratel Outgoing','INTERCONNECT','OUTGOING','Sierratel','Sierra Leone','23232'),
    ('TG-ORL-IN','Orange SL Incoming','INTERCONNECT','INCOMING','Orange SL','Sierra Leone','23278'),
    ('TG-ORL-OUT','Orange SL Outgoing','INTERCONNECT','OUTGOING','Orange SL','Sierra Leone','23278'),
    ('TG-INT-IN','International Incoming','INTERCONNECT','INCOMING','Various Carriers','International','00'),
    ('TG-INT-OUT','International Outgoing','INTERCONNECT','OUTGOING','Various Carriers','International','00'),
    ('TG-ROAM-IN','Roaming Incoming','ROAMING','INCOMING','Roaming Partners','International',''),
    ('TG-ROAM-OUT','Roaming Outgoing','ROAMING','OUTGOING','Roaming Partners','International',''),
    ('TG-GW-01','SMS Gateway 1','GATEWAY','BOTH','SMS Gateway','Sierra Leone',''),
    ('TG-GW-02','SMS Gateway 2','GATEWAY','BOTH','SMS Gateway','Sierra Leone',''),
    ('TG-INT-01','Internal MSC Link','INTERNAL','BOTH','Internal','Sierra Leone',''),
]
created = 0
for tid, name, ttype, direction, partner, country, prefix in trunk_data:
    obj, c = TrunkGroup.objects.update_or_create(
        trunk_id=tid,
        defaults={
            'name': name, 'trunk_type': ttype, 'direction': direction,
            'partner': partner, 'country': country, 'prefix': prefix, 'enabled': True
        }
    )
    if c:
        created += 1
print(f'Trunk Groups: {created} created, {TrunkGroup.objects.count()} total')
print('Seeding complete.')
