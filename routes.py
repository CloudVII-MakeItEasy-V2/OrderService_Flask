from flask import Blueprint, jsonify, request
import mysql.connector
from mysql.connector import Error

# Define a blueprint
order_blueprint = Blueprint('order_blueprint', __name__)

# Database configuration (define directly here to avoid circular import)
MYSQL_HOST = 'localhost'
MYSQL_USER = 'root'
MYSQL_PASSWORD = 'dbuserdbuser'
MYSQL_DB = 'p2_database'

# Initialize MySQL connection
def get_db_connection():
    connection = mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB
    )
    return connection

@order_blueprint.route('/', methods=['GET'])
def home():
    return "Welcome to the Orders API", 200

@order_blueprint.route('/test_db_connection', methods=['GET'])
def test_db_connection():
    try:
        # Attempt to connect to the database
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE();")
        database_name = cursor.fetchone()[0]
        cursor.close()
        connection.close()
        return jsonify({"status": "success", "database": database_name}), 200
    except Error as e:
        # If there's a connection error, return the error message
        return jsonify({"status": "failure", "error": str(e)}), 500

# Define the GET route for fetching orders
@order_blueprint.route('/orders', methods=['GET'])
def get_orders():
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM orders")  # Adjust this to match your actual table
        orders = cursor.fetchall()
        cursor.close()
        connection.close()
        return jsonify(orders)
    except Error as e:
        return jsonify({"status": "failure", "error": str(e)}), 500

# Define the POST route for creating an order
@order_blueprint.route('/orders', methods=['POST'])
def create_order():
    data = request.json
    customer_id = data['customer_id']
    status = data.get('status', 'PENDING')  # Default status if not provided

    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("INSERT INTO orders (customer_id, status) VALUES (%s, %s)", (customer_id, status))
    connection.commit()
    order_id = cursor.lastrowid
    cursor.close()
    connection.close()

    return jsonify({'id': order_id, 'customer_id': customer_id, 'status': status}), 201
