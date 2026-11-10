import base64
import os
import secrets
import tempfile

# Isolated, throwaway storage for the whole test session (set before the app starts).
os.environ['NB_DATA_DIR'] = tempfile.mkdtemp(prefix='nb-test-')
os.environ['NB_ENCRYPTION_KEY'] = base64.b64encode(secrets.token_bytes(32)).decode()

LENDER_EMAIL = 'officer@northbridge.test'
LENDER_PASSWORD = 'test-officer-password'


def lender_session(client):
    """Sign a TestClient in as a lender (created on first use) and add the CSRF header."""
    st = client.app.state.store
    if st.one('SELECT 1 FROM users WHERE email=?', (LENDER_EMAIL,)) is None:
        st.create_user(LENDER_EMAIL, 'Test Officer', LENDER_PASSWORD, 'lender')
    client.headers['x-nb-csrf'] = '1'
    assert client.post('/api/auth/login', json={'email': LENDER_EMAIL, 'password': LENDER_PASSWORD}).status_code == 200
    return client
