"""Seed NumberingPlan with ITU-T E.164 international country codes.

Idempotent — safe to re-run. Creates one entry per country code with
number_type='INTERNATIONAL'. Existing entries are updated, not duplicated.

    python manage.py seed_country_codes
    python manage.py seed_country_codes --clear   # wipe INTERNATIONAL entries first
"""
from django.core.management.base import BaseCommand
from reference.models import NumberingPlan

# (prefix, country_name)
COUNTRY_CODES = [
    # ── Zone 1 – North America ─────────────────────────────────────────────
    ('1',   'USA / Canada / Caribbean'),

    # ── Zone 2 – Africa ────────────────────────────────────────────────────
    ('20',  'Egypt'),
    ('212', 'Morocco'),
    ('213', 'Algeria'),
    ('216', 'Tunisia'),
    ('218', 'Libya'),
    ('220', 'Gambia'),
    ('221', 'Senegal'),
    ('222', 'Mauritania'),
    ('223', 'Mali'),
    ('224', 'Guinea'),
    ('225', 'Côte d\'Ivoire'),
    ('226', 'Burkina Faso'),
    ('227', 'Niger'),
    ('228', 'Togo'),
    ('229', 'Benin'),
    ('230', 'Mauritius'),
    ('231', 'Liberia'),
    ('232', 'Sierra Leone'),
    ('233', 'Ghana'),
    ('234', 'Nigeria'),
    ('235', 'Chad'),
    ('236', 'Central African Republic'),
    ('237', 'Cameroon'),
    ('238', 'Cabo Verde'),
    ('239', 'São Tomé and Príncipe'),
    ('240', 'Equatorial Guinea'),
    ('241', 'Gabon'),
    ('242', 'Republic of the Congo'),
    ('243', 'DR Congo'),
    ('244', 'Angola'),
    ('245', 'Guinea-Bissau'),
    ('248', 'Seychelles'),
    ('249', 'Sudan'),
    ('250', 'Rwanda'),
    ('251', 'Ethiopia'),
    ('252', 'Somalia'),
    ('253', 'Djibouti'),
    ('254', 'Kenya'),
    ('255', 'Tanzania'),
    ('256', 'Uganda'),
    ('257', 'Burundi'),
    ('258', 'Mozambique'),
    ('260', 'Zambia'),
    ('261', 'Madagascar'),
    ('262', 'Réunion / Mayotte'),
    ('263', 'Zimbabwe'),
    ('264', 'Namibia'),
    ('265', 'Malawi'),
    ('266', 'Lesotho'),
    ('267', 'Botswana'),
    ('268', 'Eswatini'),
    ('269', 'Comoros'),
    ('27',  'South Africa'),
    ('290', 'Saint Helena'),
    ('291', 'Eritrea'),
    ('297', 'Aruba'),
    ('298', 'Faroe Islands'),
    ('299', 'Greenland'),

    # ── Zone 3 – Europe (South/East) ───────────────────────────────────────
    ('30',  'Greece'),
    ('31',  'Netherlands'),
    ('32',  'Belgium'),
    ('33',  'France'),
    ('34',  'Spain'),
    ('36',  'Hungary'),
    ('39',  'Italy'),
    ('40',  'Romania'),
    ('41',  'Switzerland'),
    ('43',  'Austria'),
    ('44',  'United Kingdom'),
    ('45',  'Denmark'),
    ('46',  'Sweden'),
    ('47',  'Norway'),
    ('48',  'Poland'),
    ('49',  'Germany'),
    ('350', 'Gibraltar'),
    ('351', 'Portugal'),
    ('352', 'Luxembourg'),
    ('353', 'Ireland'),
    ('354', 'Iceland'),
    ('355', 'Albania'),
    ('356', 'Malta'),
    ('357', 'Cyprus'),
    ('358', 'Finland'),
    ('359', 'Bulgaria'),
    ('370', 'Lithuania'),
    ('371', 'Latvia'),
    ('372', 'Estonia'),
    ('373', 'Moldova'),
    ('374', 'Armenia'),
    ('375', 'Belarus'),
    ('376', 'Andorra'),
    ('377', 'Monaco'),
    ('380', 'Ukraine'),
    ('381', 'Serbia'),
    ('382', 'Montenegro'),
    ('385', 'Croatia'),
    ('386', 'Slovenia'),
    ('387', 'Bosnia and Herzegovina'),
    ('389', 'North Macedonia'),

    # ── Zone 5 – Americas (South/Central) ──────────────────────────────────
    ('52',  'Mexico'),
    ('53',  'Cuba'),
    ('54',  'Argentina'),
    ('55',  'Brazil'),
    ('56',  'Chile'),
    ('57',  'Colombia'),
    ('58',  'Venezuela'),
    ('591', 'Bolivia'),
    ('592', 'Guyana'),
    ('593', 'Ecuador'),
    ('595', 'Paraguay'),
    ('597', 'Suriname'),
    ('598', 'Uruguay'),

    # ── Zone 6 – South-East Asia & Pacific ─────────────────────────────────
    ('60',  'Malaysia'),
    ('61',  'Australia'),
    ('62',  'Indonesia'),
    ('63',  'Philippines'),
    ('64',  'New Zealand'),
    ('65',  'Singapore'),
    ('66',  'Thailand'),
    ('670', 'Timor-Leste'),
    ('675', 'Papua New Guinea'),
    ('676', 'Tonga'),
    ('677', 'Solomon Islands'),
    ('678', 'Vanuatu'),
    ('679', 'Fiji'),
    ('680', 'Palau'),
    ('682', 'Cook Islands'),
    ('685', 'Samoa'),
    ('686', 'Kiribati'),
    ('687', 'New Caledonia'),
    ('688', 'Tuvalu'),
    ('689', 'French Polynesia'),
    ('690', 'Tokelau'),
    ('691', 'Micronesia'),
    ('692', 'Marshall Islands'),

    # ── Zone 7 – Russia / CIS ──────────────────────────────────────────────
    ('7',   'Russia / Kazakhstan'),

    # ── Zone 8 – East Asia ─────────────────────────────────────────────────
    ('81',  'Japan'),
    ('82',  'South Korea'),
    ('84',  'Vietnam'),
    ('86',  'China'),
    ('852', 'Hong Kong'),
    ('853', 'Macau'),
    ('855', 'Cambodia'),
    ('856', 'Laos'),
    ('880', 'Bangladesh'),
    ('886', 'Taiwan'),

    # ── Zone 9 – Middle East & South/Central Asia ──────────────────────────
    ('90',  'Turkey'),
    ('91',  'India'),
    ('92',  'Pakistan'),
    ('93',  'Afghanistan'),
    ('94',  'Sri Lanka'),
    ('95',  'Myanmar'),
    ('960', 'Maldives'),
    ('961', 'Lebanon'),
    ('962', 'Jordan'),
    ('963', 'Syria'),
    ('964', 'Iraq'),
    ('965', 'Kuwait'),
    ('966', 'Saudi Arabia'),
    ('967', 'Yemen'),
    ('968', 'Oman'),
    ('970', 'Palestinian Territory'),
    ('971', 'United Arab Emirates'),
    ('972', 'Israel'),
    ('973', 'Bahrain'),
    ('974', 'Qatar'),
    ('975', 'Bhutan'),
    ('976', 'Mongolia'),
    ('977', 'Nepal'),
    ('98',  'Iran'),
    ('992', 'Tajikistan'),
    ('993', 'Turkmenistan'),
    ('994', 'Azerbaijan'),
    ('995', 'Georgia'),
    ('996', 'Kyrgyzstan'),
    ('998', 'Uzbekistan'),
]


class Command(BaseCommand):
    help = 'Seed NumberingPlan with ITU-T international country codes (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear', action='store_true',
            help='Delete existing INTERNATIONAL-type entries before seeding.',
        )

    def handle(self, *args, **opts):
        if opts['clear']:
            deleted, _ = NumberingPlan.objects.filter(number_type='INTERNATIONAL').delete()
            self.stdout.write(f'Cleared {deleted} existing INTERNATIONAL entries.')

        created_count = updated_count = 0
        for prefix, country in COUNTRY_CODES:
            _, created = NumberingPlan.objects.update_or_create(
                prefix=prefix,
                defaults=dict(
                    country=country,
                    country_code=prefix,
                    operator='Various',
                    number_type='INTERNATIONAL',
                    min_length=7,
                    max_length=15,
                    enabled=True,
                ),
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. Created {created_count}, updated {updated_count} country code entries '
            f'({len(COUNTRY_CODES)} total).'
        ))
