from flask import Flask
from routes import order_blueprint

app = Flask(__name__)

# Register the blueprint
app.register_blueprint(order_blueprint)

if __name__ == '__main__':
    app.run(debug=True)
