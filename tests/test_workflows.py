from pathlib import Path
import tempfile
import unittest
from app.journal import Journal
from app.storage import DataError, Store

DATA = Path(__file__).parents[1] / 'data'


class BusinessChainTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)
        self.journal = Journal()
        from app import planning
        self.basis = planning.compare(self.store, self.journal, {})['basis']

    def tearDown(self):
        self.journal.close()

    def test_business_chain(self):
        self.exercise(self.store, self.journal)

    def test_rollback_on_failure(self):
        with self.assertRaises(DataError):
            with self.journal.transaction():
                self.journal.put('test', 'a', {'value': 1})
                raise DataError('aborted')
        self.assertEqual(self.journal.all('test'), [])

    def test_restart_retains_business_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workflow.sqlite3'
            journal = Journal(path)
            try:
                self.exercise(self.store, journal)
                before = {kind[0] for kind in journal.connection.execute(
                    'SELECT DISTINCT kind FROM documents')}
                snapshot = {kind: journal.all(kind) for kind in before}
            finally:
                journal.close()
            reopened = Journal(path)
            try:
                self.assertEqual(snapshot, {kind: reopened.all(kind)
                                            for kind in before})
            finally:
                reopened.close()

    def exercise(self, store, journal):
        from app import planning, funding, resources
        basis = planning.compare(store, journal, {})['basis']
        planning.nominate(store, journal, dict(
            nomination='n1', proposal='nearby', resource='hall-team',
            basis=basis))
        funding.approve(store, journal, dict(nomination='n1', amount=30000))
        self.assertEqual(resources.activate(
            store, journal, dict(nomination='n1'))['status'], 'active')
        planning.nominate(store, journal, dict(
            nomination='n2', proposal='remote', resource='hall-team',
            basis=basis))
        funding.approve(store, journal, dict(nomination='n2', amount=20000))
        with self.assertRaises(DataError):
            resources.activate(store, journal, dict(nomination='n2'))
        self.assertEqual(len(journal.all('active_site')), 1)


class EvidenceGuardTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)
        self.journal = Journal()
        from app import planning
        self.basis = planning.compare(self.store, self.journal, {})['basis']

    def tearDown(self):
        self.journal.close()

    def _nominate(self, nomination, proposal, resource='mobile-team'):
        from app import planning
        return planning.nominate(self.store, self.journal, dict(
            nomination=nomination, proposal=proposal, resource=resource,
            basis=self.basis))

    def test_nomination_requires_existing_basis(self):
        from app import planning
        with self.assertRaises(DataError):
            planning.nominate(self.store, self.journal, dict(
                nomination='x', proposal='nearby', resource='hall-team',
                basis='basis-missing'))

    def test_proposal_outside_basis_scope_rejected(self):
        from app import planning
        narrow = planning.compare(self.store, self.journal,
                                  {'proposals': ['nearby']})['basis']
        with self.assertRaises(DataError):
            planning.nominate(self.store, self.journal, dict(
                nomination='x', proposal='remote', resource='mobile-team',
                basis=narrow))

    def test_stale_basis_blocks_approval(self):
        from app import funding
        self._nominate('x', 'nearby', 'hall-team')
        row = self.journal.get('nomination', 'x')
        row['basis_fingerprint'] = '0' * 64
        self.journal.put('nomination', 'x', row)
        with self.assertRaises(DataError):
            funding.approve(self.store, self.journal,
                            dict(nomination='x', amount=10000))

    def test_duplicate_benefit_blocks_second_grant(self):
        from app import funding
        first = self._nominate('g1', 'nearby', 'hall-team')
        funding.approve(self.store, self.journal,
                        dict(nomination='g1', amount=10000))
        # 手工构造一份受影响人群与 g1 重叠的提名，模拟同一批人重复受益。
        overlap = dict(first)
        overlap['nomination'] = 'g2'
        overlap['resource'] = 'mobile-team'
        overlap['evidence'] = dict(first['evidence'])
        overlap['evidence']['affected_groups'] = list(
            first['evidence']['affected_groups'])
        self.journal.put('nomination', 'g2', overlap)
        with self.assertRaises(DataError):
            funding.approve(self.store, self.journal,
                            dict(nomination='g2', amount=10000))

    def test_grant_without_service_evidence_rejected(self):
        from app import funding
        self._nominate('z', 'nearby', 'hall-team')
        row = self.journal.get('nomination', 'z')
        row['evidence'] = dict(row['evidence'], gain_cells=[0] * 9,
                               gap_improvement_cells=[0] * 9)
        self.journal.put('nomination', 'z', row)
        with self.assertRaises(DataError):
            funding.approve(self.store, self.journal,
                            dict(nomination='z', amount=10000))

    def test_activate_requires_matching_grant_target(self):
        from app import funding, resources
        self._nominate('a1', 'remote')
        funding.approve(self.store, self.journal,
                        dict(nomination='a1', amount=10000))
        grant = self.journal.get('grant', 'a1')
        grant['resource'] = 'hall-team'
        self.journal.put('grant', 'a1', grant)
        with self.assertRaises(DataError):
            resources.activate(self.store, self.journal,
                               dict(nomination='a1'))

    def test_grant_and_active_site_remain_traceable_to_groups(self):
        from app import funding, resources
        self._nominate('t1', 'remote')
        funding.approve(self.store, self.journal,
                        dict(nomination='t1', amount=12000))
        grant = self.journal.get('grant', 't1')
        self.assertEqual(grant['beneficiary_groups'], ['P10'])
        self.assertEqual(grant['beneficiary_detail'][0]['group'], 'P10')
        self.assertEqual(grant['gain_range'], [24, 24])
        active = resources.activate(self.store, self.journal,
                                    dict(nomination='t1'))
        self.assertEqual(active['beneficiary_groups'],
                         grant['beneficiary_groups'])
        self.assertEqual(active['resource'], grant['resource'])
        self.assertEqual(active['proposal'], grant['proposal'])
        self.assertEqual(active['grant_amount'], grant['amount'])

    def test_compare_is_idempotent_and_persisted(self):
        from app import planning
        first = planning.compare(self.store, self.journal,
                                 {'proposals': ['nearby']})
        second = planning.compare(self.store, self.journal,
                                  {'proposals': ['nearby']})
        self.assertEqual(first['basis'], second['basis'])
        self.assertEqual(self.journal.get('comparison', first['basis']),
                         first)


if __name__ == '__main__':
    unittest.main()
