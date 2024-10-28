from flask import Blueprint, jsonify, request
import mysql.connector
from mysql.connector import Error

# Define a blueprint
order_blueprint = Blueprint('order_blueprint', __name__)

# Database configuration (define directly here to avoid circular import)
MYSQL_HOST = 'makeiteasy.ck0scewemjwp.us-east-1.rds.amazonaws.com'
MYSQL_USER = 'root'
MYSQL_PASSWORD = 'dbuserdbuser'
MYSQL_DB = 'Order_Service'

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
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    # Fetch orders and their corresponding items
    try:
        cursor.execute("SELECT * FROM `Order`")
        orders = cursor.fetchall()

        # For each order, fetch the associated items
        for order in orders:
            cursor.execute("SELECT * FROM OrderItem WHERE order_id = %s", (order['order_id'],))
            order['items'] = cursor.fetchall()
    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500
    finally:
        cursor.close()
        connection.close()

    return jsonify(orders)

# Define the POST route for creating an order
@order_blueprint.route('/create_order', methods=['POST'])
def create_order():
    data = request.json
    customer_id = data['customer_id']
    status = data.get('status', 'Pending')  # Default status to 'Pending'
    tracking_number = data.get('tracking_number', None)

    # Items associated with the order
    items = data.get('items', [])  # List of items with product_id, quantity, and price

    # Calculate the total amount
    total_amount = sum(item['price'] * item['quantity'] for item in items)

    # Connect to the database and insert the order
    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        # Insert into `Order` table
        cursor.execute("""
            INSERT INTO `Order` (customer_id, total_amount, status, tracking_number)
            VALUES (%s, %s, %s, %s)
        """, (customer_id, total_amount, status, tracking_number))
        connection.commit()
        order_id = cursor.lastrowid

        # Insert each item into the `OrderItem` table
        for item in items:
            product_id = item['product_id']
            quantity = item['quantity']
            price = item['price']
            cursor.execute("""
                INSERT INTO OrderItem (order_id, product_id, quantity, price)
                VALUES (%s, %s, %s, %s)
            """, (order_id, product_id, quantity, price))

        connection.commit()
    except mysql.connector.Error as err:
        # Handle database errors
        connection.rollback()
        return jsonify({"error": str(err)}), 500
    finally:
        cursor.close()
        connection.close()

    # Return the created order with the total amount and items
    return jsonify({
        'order_id': order_id,
        'customer_id': customer_id,
        'total_amount': str(total_amount),  # Decimal values as string for JSON compatibility
        'status': status,
        'tracking_number': tracking_number,
        'items': items
    }), 201
