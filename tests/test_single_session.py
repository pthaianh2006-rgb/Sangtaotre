"""Temporary DB and synthetic camera data only; never opens live devices."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

STAGE = Path(__file__).resolve().parents[1]/'aclproject2-main'/'aclproject2-main'
os.environ['KNEEROM_TEST_APP'] = str(STAGE)
sys.path.insert(0, str(Path(__file__).parent))
import test_camera_quality_combined as existing
b = existing.b


class SingleSessionTests(existing.PairedRoutes):
    def setUp(self):
        super().setUp()
        b._measurement_drafts.clear()

    def tearDown(self):
        b._measurement_drafts.clear()
        super().tearDown()

    def freeze(self):
        self.seed()
        response = self.post('/measurement/stop', json={'preserve': True})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['can_save'])
        return response.json

    def save(self, draft):
        return self.post('/measurement/save_combined', json={'draft_token': draft['draft_token']})

    def test_stop_freezes_both_sources_then_save_without_live_hardware(self):
        draft = self.freeze()
        self.assertEqual(self.counts(), [0, 0], 'stop must not write a half session')
        self.assertIsNone(b._hardware_owner)
        self.assertEqual((b.max_rom_session, b.imu_max_rom), (0, 0))
        self.assertFalse(self.client.get('/measurement/status').json['active'])
        with self.client.get(draft['snapshot_url']) as image:
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.mimetype, 'image/jpeg')
        # The user can review a stopped result, even after live freshness windows expire.
        b.imu_connected = False
        b.imu_calibrating = True
        with patch.object(b.time, 'monotonic', return_value=time.monotonic()+20):
            response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual((response.json['camera_rom'], response.json['imu_rom']), (82, 85))
        self.assertEqual(response.json['time'], draft['time'])
        self.assertEqual(self.counts(), [2, 1])
        self.assertEqual(self.client.get(draft['snapshot_url']).status_code, 404)

    def test_another_patient_can_measure_while_first_reviews_and_saves(self):
        draft = self.freeze()
        other = self.client_for('patient', 2)
        response = other.post('/measurement/start', json={'combined': True}, headers={'X-CSRF-Token': 'test-token'})
        self.assertEqual(response.status_code, 200)
        b.max_rom_session, b.imu_max_rom = 14, 17
        self.assertEqual(self.save(draft).status_code, 200)
        self.assertEqual((b._hardware_owner, b.max_rom_session, b.imu_max_rom), (2, 14, 17))

    def test_stop_and_save_retries_are_idempotent(self):
        draft = self.freeze()
        repeated = self.post('/measurement/stop', json={'preserve': True})
        self.assertEqual(repeated.json['draft_token'], draft['draft_token'])
        first = self.save(draft)
        second = self.save(draft)
        self.assertEqual(first.json, second.json)
        self.assertEqual(self.counts(), [2, 1])
        self.assertEqual(len(list(b.CARE_UPLOAD_DIR.glob('*.jpg'))), 1)

    def test_retry_stop_recovers_when_response_cookie_was_lost(self):
        draft = self.freeze()
        with self.client.session_transaction() as s:
            s.pop('measurement_draft', None)
        response = self.post('/measurement/stop', json={'preserve': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['draft_token'], draft['draft_token'])
        self.assertEqual(self.counts(), [0, 0])

    def test_draft_is_private_even_after_device_handoff(self):
        draft = self.freeze()
        for role, uid in [('patient', 2), ('doctor', 1)]:
            other = self.client_for(role, uid)
            self.assertIn(other.get(draft['snapshot_url']).status_code, (403, 404))
            response = other.post('/measurement/save_combined', json={'draft_token': draft['draft_token']}, headers={'X-CSRF-Token': 'test-token'})
            self.assertIn(response.status_code, (403, 409))
        same_patient = self.client_for('patient', 1)
        with same_patient.session_transaction() as s:
            s['csrf_token'] = 'another-browser'
        self.assertEqual(same_patient.get(draft['snapshot_url']).status_code, 404)
        self.assertEqual(same_patient.post('/measurement/save_combined', json={'draft_token': draft['draft_token']}, headers={'X-CSRF-Token': 'another-browser'}).status_code, 409)
        self.assertEqual(self.counts(), [0, 0])

    def test_invalid_source_stop_releases_hardware_but_cannot_save(self):
        for broken in ('camera', 'imu', 'calibrating'):
            self.post('/measurement/start', json={'combined': True})
            self.seed()
            if broken == 'camera': b.camera_last_data -= 5
            if broken == 'imu': b.imu_last_data -= 5
            if broken == 'calibrating': b.imu_calibrating = True
            response = self.post('/measurement/stop', json={'preserve': True})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json['can_save'])
            self.assertNotIn('draft_token', response.json)
            self.assertIsNone(b._hardware_owner)
        self.assertEqual(self.counts(), [0, 0])

    def test_failed_save_retains_frozen_data_for_retry(self):
        draft = self.freeze()
        original = b.insert_measurement
        def fail_second(conn, pid, value, when, source):
            if source == 'imu': raise b.DatabaseError[0]('simulated')
            return original(conn, pid, value, when, source)
        with patch.object(b, 'insert_measurement', side_effect=fail_second), self.assertLogs(b.logger, level='ERROR'):
            self.assertEqual(self.save(draft).status_code, 503)
        self.assertEqual(self.counts(), [0, 0])
        self.assertEqual(self.client.get(draft['snapshot_url']).status_code, 200)
        self.assertEqual(self.save(draft).status_code, 200)
        self.assertEqual(self.counts(), [2, 1])

    def test_expired_draft_and_logout_and_new_start_clear_private_capture(self):
        draft = self.freeze()
        with patch.object(b.time, 'monotonic', return_value=time.monotonic()+601):
            self.assertEqual(self.save(draft).status_code, 409)
        self.post('/measurement/start', json={'combined': True})
        draft = self.freeze()
        self.post('/measurement/start', json={'combined': True})
        self.assertEqual(self.save(draft).status_code, 409)
        draft = self.freeze()
        self.client.get('/logout')
        self.assertNotIn(draft['draft_token'], b._measurement_drafts)

    def test_plain_stop_does_not_prepare_save(self):
        self.seed()
        self.post('/measurement/stop')
        self.assertFalse(b._measurement_drafts)
        self.assertEqual(self.post('/measurement/save_combined').status_code, 409)

    def test_invalid_tokens_and_request_shapes_are_rejected(self):
        self.assertEqual(self.post('/measurement/save_combined', json={'draft_token': []}).status_code, 400)
        for route in ('/measurement/start', '/measurement/stop', '/measurement/save_combined'):
            self.assertEqual(self.post(route, json=['bad']).status_code, 400)

    def test_peak_from_other_generation_never_saved(self):
        self.seed()
        b._camera_peak['generation'] -= 1
        self.assertEqual(self.post('/measurement/save_combined').status_code, 409)
        self.assertEqual(self.counts(), [0, 0])

    def test_draft_capacity_is_bounded_and_stop_is_not_lost(self):
        self.freeze()
        token = next(iter(b._measurement_drafts))
        template = b._measurement_drafts[token]
        for index in range(b.MAX_MEASUREMENT_DRAFTS):
            b._measurement_drafts[str(index)] = dict(template, owner=2)
        self.post('/measurement/start', json={'combined': True})
        self.seed()
        self.assertEqual(self.post('/measurement/stop', json={'preserve': True}).status_code, 503)
        self.assertEqual(b._hardware_owner, 1)
        self.assertEqual(self.counts(), [0, 0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
