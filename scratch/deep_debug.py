import app
from app import app as flask_app
from flask import render_template

with flask_app.app_context():
    try:
        from app import home
        # We can't easily call the route function because it depends on request
        # but we can try to render the template directly with mock data
        from database import get_db
        db = get_db()
        cursor = db.entries.find(sort=[("dateString", -1)]).limit(300)
        entries = []
        for doc in cursor:
            doc['_id'] = str(doc['_id'])
            doc['bg_value'] = doc.get('sgv') or 0
            doc['date_str'] = doc.get('dateString', '')
            entries.append(doc)
        
        print("Rendering template...")
        html = render_template('index.html', entries=entries, chart_data=list(reversed(entries)))
        print("Success!")
    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()
