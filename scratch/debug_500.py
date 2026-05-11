import app
from app import app as flask_app

with flask_app.test_client() as client:
    response = client.get('/')
    print(f"Status Code: {response.status_code}")
    if response.status_code == 500:
        # Flask usually doesn't return the traceback in the response body unless configured
        # but let's see what's there
        print(response.data.decode('utf-8'))
