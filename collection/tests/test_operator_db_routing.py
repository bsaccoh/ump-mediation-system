"""DJ Phase 3 — per-operator database routing + isolation."""
from django.test import TestCase

from config.db_router import ServiceRouter
from core.operator_context import operator_context, get_operator
from collection.models import CDRFile
from streams.msc.models import MSCRecord

from interconnect.tests._fixtures import make_msc_record


class OperatorDbRoutingTests(TestCase):
    databases = {
        'default', 'mediation_orange', 'mediation_africell', 'mediation_qcell',
        'interconnect', 'regulatory', 'roaming',
    }

    def test_router_picks_mediation_alias_by_active_operator(self):
        r = ServiceRouter()
        # Home operator (orange) keeps using the existing 'default' DB.
        with operator_context('orange'):
            self.assertEqual(r.db_for_write(MSCRecord), 'default')
            self.assertEqual(r.db_for_read(MSCRecord), 'default')
        # Additional operators are isolated into their own DB.
        with operator_context('africell'):
            self.assertEqual(r.db_for_write(MSCRecord), 'mediation_africell')

    def test_control_plane_models_stay_default(self):
        r = ServiceRouter()
        with operator_context('africell'):
            # CDRFile (collection) is control-plane regardless of active operator.
            self.assertEqual(r.db_for_write(CDRFile), 'default')

    def test_records_are_isolated_per_operator(self):
        with operator_context('orange'):       # home operator -> default
            make_msc_record('MOC')
        with operator_context('africell'):     # additional operator -> mediation_africell
            make_msc_record('MOC')
            make_msc_record('MTC')

        self.assertEqual(MSCRecord.objects.using('default').count(), 1)
        self.assertEqual(MSCRecord.objects.using('mediation_africell').count(), 2)
        # No bleed-through into another operator's DB.
        self.assertEqual(MSCRecord.objects.using('mediation_qcell').count(), 0)

    def test_default_operator_used_when_no_context(self):
        from django.conf import settings
        self.assertEqual(get_operator(), settings.DEFAULT_OPERATOR)


class CDRFileAddressingTests(TestCase):
    databases = {'default', 'mediation_africell'}

    def test_cdrfile_in_default_records_reference_it_cross_db(self):
        # CDRFile lives in default; an additional operator's MSCRecord lives in
        # mediation_africell and references the file by id with no hard FK
        # constraint (db_constraint=False).
        cdr = CDRFile.objects.create(
            filename='AFC_x.dat', file_path='/dev/null', file_size=1,
            status='COMPLETED', operator_code='africell', vendor='huawei',
            network_element='msc',
        )
        with operator_context('africell'):
            rec = make_msc_record('MOC', file=cdr)
        self.assertEqual(
            MSCRecord.objects.using('mediation_africell').get(pk=rec.pk).file_id,
            cdr.pk,
        )
