import _testenv  # noqa: F401  (isolated test database; must be first)
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from main import app


class TestApiIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()          # run startup (create tables, seed admin)
        res = cls.client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
        assert res.status_code == 200, res.text
        cls.token = res.json()['token']
        # The isolated test database starts empty: load the SYNTHETIC sample file.
        with open(_testenv.SAMPLE_CSV, 'rb') as f:
            up = cls.client.post('/api/upload', files={'file': ('sample_inventory.csv', f, 'text/csv')},
                                 headers={'Authorization': f'Bearer {cls.token}'})
        assert up.status_code == 200, up.text

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _get(self, path):
        headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
        resp = self.client.get(path, headers=headers)
        return resp.status_code, resp.json()

    def _post(self, path, data_dict):
        headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
        resp = self.client.post(path, json=data_dict, headers=headers)
        return resp.status_code, resp.json()


    def test_health(self):
        resp = self.client.get('/api/health')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['version'], '2.4.0')


    def test_dashboard_summary(self):
        status, data = self._get('/api/dashboard/latest/summary')
        self.assertEqual(status, 200)
        self.assertIn('kpis', data)
        self.assertGreaterEqual(data['kpis']['total_skus'], 1)

    def test_optimization_comparison(self):
        status, data = self._get('/api/optimization/comparison?batch_id=latest')
        self.assertEqual(status, 200)
        self.assertIn('current_policy', data)
        self.assertIn('eoq_policy', data)
        self.assertIn('constrained_policy', data)

    def test_eidsf_policy_on_dashboard_records(self):
        """The article's engine runs on the tenant's records; the returned policy is checked against
        the model and the Lagrangian lower bound (convex case: certified gap)."""
        status, data = self._post('/api/eidsf/policy', {'method': 'FMCG-ABC', 'seed': 3, 'max_evals': 2000})
        self.assertEqual(status, 200, data)
        self.assertTrue(data['engine'].startswith('eidsf/'))
        self.assertGreater(len(data['skus']), 0)
        if data['feasible'] and data['lower_bound'] is not None:
            self.assertGreaterEqual(data['total_cost'], data['lower_bound'] * (1 - 1e-9))
            self.assertGreaterEqual(data['certified_gap_pct'], -1e-6)
        for a in data['skus']:
            total = a['ordering_cost'] + a['holding_cost'] + a['shortage_cost'] + a['purchasing_cost']
            self.assertAlmostEqual(total, a['total_cost'], places=4)
            for r in a['rules_fired']:
                self.assertNotIn(r['id'], ('R1', 'R6'))      # need replay; never fired without evidence
        self.assertIn('R1', data['rules_not_evaluated'])
        self.assertEqual(data['resources'][0]['resource'], 'procurement budget')
        for r in data['resources']:
            self.assertAlmostEqual(r['utilisation'], r['used'] / r['capacity'], places=9)
            if r['shadow_price'] is not None:
                self.assertGreaterEqual(r['shadow_price'], 0.0)
        self.assertTrue(data['assumptions'])
        bad, _ = self._post('/api/eidsf/policy', {'method': 'XYZ'})
        self.assertEqual(bad, 400)

    def test_forecasting_overview(self):
        status, data = self._get('/api/forecasting/overview?batch_id=latest')
        self.assertEqual(status, 200)
        self.assertIn('overall_metrics', data)
        self.assertIn('total_items_forecasted', data)

    def test_scenario_simulation(self):
        status, data = self._post('/api/scenarios/simulate', {
            'scenario_name': 'Automated Test Spike',
            'demand_pct_change': 25.0,
            'lead_time_days_add': 5.0
        })
        self.assertEqual(status, 200)
        self.assertIn('baseline_metrics', data)
        self.assertIn('simulated_metrics', data)

    def test_procurement_plan(self):
        status, data = self._get('/api/procurement/plan?batch_id=latest')
        self.assertEqual(status, 200)
        self.assertIn('recommended_orders', data)

    def test_org_config(self):
        status, data = self._get('/api/org/config')
        self.assertEqual(status, 200)
        self.assertIn('service_level_target', data)


if __name__ == "__main__":
    unittest.main()
