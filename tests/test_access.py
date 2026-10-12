import os
from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from core.access import password_hash, verify_password, parse_hash, session_valid, AttemptLimiter

APP=str(Path(__file__).resolve().parents[1]/'app.py')
PASSWORD='synthetic-test-only-passphrase'


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded=password_hash(PASSWORD)

    def test_hash_verification_rotation_and_expiry(self):
        self.assertTrue(verify_password(PASSWORD,self.encoded))
        self.assertFalse(verify_password('wrong-test-passphrase',self.encoded))
        import hashlib
        session={'hash':hashlib.sha256(self.encoded.encode()).hexdigest(),'time':100}
        self.assertTrue(session_valid(session,self.encoded,now=101))
        self.assertFalse(session_valid(session,self.encoded,now=14500))
        self.assertFalse(session_valid(session,password_hash(PASSWORD),now=101))
        for invalid in ('','plaintext','pbkdf2_sha256$1$aa$bb'):
            with self.assertRaises(ValueError): parse_hash(invalid)

    def test_login_attempts_are_limited_across_sessions(self):
        limiter=AttemptLimiter()
        for i in range(5): self.assertEqual(limiter.check('incorrect',self.encoded,now=i),'invalid')
        self.assertEqual(limiter.check(PASSWORD,self.encoded,now=5),'wait')
        self.assertEqual(limiter.check(PASSWORD,self.encoded,now=65),'ok')

    def test_online_missing_config_blocks_before_database_access(self):
        with patch.dict(os.environ,{'APP_MODE':'online-test','TEST_PASSWORD_HASH':''}), patch('ui.common.db') as db:
            at=AppTest.from_file(APP).run()
            self.assertEqual(len(at.exception),0)
            self.assertTrue(any('尚未配置' in e.value for e in at.error))
            db.assert_not_called()

    def test_render_environment_cannot_bypass_access(self):
        with patch.dict(os.environ,{'APP_MODE':'local','RENDER':'true','TEST_PASSWORD_HASH':''}), patch('ui.common.db') as db:
            at=AppTest.from_file(APP).run()
            self.assertTrue(any('尚未配置' in e.value for e in at.error))
            db.assert_not_called()

    def test_login_and_logout_gate_all_pages(self):
        with patch.dict(os.environ,{'APP_MODE':'online-test','TEST_PASSWORD_HASH':self.encoded}), patch('ui.common.db',return_value=[]) as db:
            at=AppTest.from_file(APP).run()
            self.assertEqual(len(at.exception),0)
            db.assert_not_called()
            at.text_input[0].set_value('incorrect-password')
            at.button[0].click().run()
            self.assertTrue(any('不正确' in e.value for e in at.error))
            db.assert_not_called()
            at.text_input[0].set_value(PASSWORD)
            at.button[0].click().run()
            self.assertEqual(len(at.exception),0)
            self.assertTrue(db.called)
            db.reset_mock()
            at.switch_page('views/stores_page.py').run()
            self.assertEqual(len(at.exception),0)
            next(b for b in at.button if b.label=='退出测试').click().run()
            db.reset_mock()
            at.run()
            self.assertEqual(at.text_input[0].label,'测试口令')
            db.assert_not_called()
