from flask import Flask
from routes import order_blueprint
import os

app = Flask(__name__)

# Register the blueprint
app.register_blueprint(order_blueprint)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
