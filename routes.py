from flask import Blueprint, jsonify, request, url_for, make_response
import mysql.connector
from mysql.connector import Error
from datetime import datetime
from threading import Thread
import requests
import time
import os

# Database configuration using environment variables
# MYSQL_HOST = os.getenv('MYSQL_HOST')
# MYSQL_USER = os.getenv('MYSQL_USER')
# MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
# MYSQL_DB = os.getenv('MYSQL_DB')
MYSQL_HOST = "makeiteasy.ck0scewemjwp.us-east-1.rds.amazonaws.com"
MYSQL_USER = "root"
MYSQL_PASSWORD = "dbuserdbuser"
MYSQL_DB = "Order_Service"


# Define a blueprint
order_blueprint = Blueprint('order_blueprint', __name__)

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
    page = request.args.get('page', default=1, type=int)
    page_size = request.args.get('page_size', default=10, type=int)
    customer_id = request.args.get('customer_id', type=int)

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # Query orders with optional customer_id filter
        query = "SELECT * FROM `Order`"
        params = []
        if customer_id:
            query += " WHERE customer_id = %s"
            params.append(customer_id)
        query += " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])

        cursor.execute(query, tuple(params))
        orders = cursor.fetchall()

        # Fetch associated items for each order
        for order in orders:
            cursor.execute("SELECT * FROM OrderItem WHERE order_id = %s", (order['order_id'],))
            order['items'] = cursor.fetchall()

        response = make_response(jsonify(orders), 200)
        
        # Add Link header for pagination
        next_url = url_for('order_blueprint.get_orders', page=page + 1, page_size=page_size, customer_id=customer_id)
        response.headers['Link'] = f'<{next_url}>; rel="next"'

    except mysql.connector.Error as err:
        response = jsonify({"error": str(err)})
        response.status_code = 500
    finally:
        cursor.close()
        connection.close()

    return response

# POST route to create a new order
@order_blueprint.route('/create_order', methods=['POST'])
def create_order():
    data = request.json
    customer_id = data['customer_id']
    status = data.get('status', 'Pending')  # Default status
    tracking_number = data.get('tracking_number', None)
    items = data.get('items', [])  # List of items with product_id, quantity, and price

    # Calculate total amount
    total_amount = sum(item['price'] * item['quantity'] for item in items)

    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        # Insert into Order table
        cursor.execute("""
            INSERT INTO `Order` (customer_id, total_amount, status, tracking_number)
            VALUES (%s, %s, %s, %s)
        """, (customer_id, total_amount, status, tracking_number))
        connection.commit()
        order_id = cursor.lastrowid

        # Insert each item into OrderItem table
        for item in items:
            cursor.execute("""
                INSERT INTO OrderItem (order_id, product_id, quantity, price)
                VALUES (%s, %s, %s, %s)
            """, (order_id, item['product_id'], item['quantity'], item['price']))
        
        connection.commit()

        # Create response with Link header to newly created order
        response = make_response(jsonify({
            'order_id': order_id,
            'customer_id': customer_id,
            'total_amount': str(total_amount),  # Convert decimal to string for JSON
            'status': status,
            'tracking_number': tracking_number,
            'items': items
        }), 201)
        response.headers['Link'] = url_for('order_blueprint.get_order', order_id=order_id)
    except mysql.connector.Error as err:
        connection.rollback()
        response = jsonify({"error": str(err)})
        response.status_code = 500
    finally:
        cursor.close()
        connection.close()

    return response

@order_blueprint.route('/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # Fetch the order with the given order_id
        cursor.execute("SELECT * FROM `Order` WHERE order_id = %s", (order_id,))
        order = cursor.fetchone()

        if not order:
            return jsonify({"error": "Order not found"}), 404

        # Fetch the associated order items
        cursor.execute("SELECT * FROM OrderItem WHERE order_id = %s", (order_id,))
        order['items'] = cursor.fetchall()

    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500
    finally:
        cursor.close()
        connection.close()

    return jsonify(order)

@order_blueprint.route('/create_order/async', methods=['POST'])
def create_order_async():
    data = request.json
    customer_id = data.get('customer_id')
    status = data.get('status', "Processing")
    items = data.get('items', [])
    total_amount = sum(item['price'] * item['quantity'] for item in items)
    callback_url = data.get('callback_url')  # Client callback URL

    # Start async processing in a separate thread
    thread = Thread(target=process_order, args=(customer_id, status, items, total_amount, callback_url))
    thread.start()

    return jsonify({'status': 'Order is being processed'}), 202

def process_order(customer_id, status, items, total_amount, callback_url):
    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        # Insert order with "PROCESSING" status
        cursor.execute(
            "INSERT INTO `Order` (customer_id, total_amount, status) VALUES (%s, %s, %s)",
            (customer_id, total_amount, "PROCESSING")
        )
        connection.commit()
        order_id = cursor.lastrowid

        # Insert each item in the order
        for item in items:
            cursor.execute(
                "INSERT INTO OrderItem (order_id, product_id, quantity, price) VALUES (%s, %s, %s, %s)",
                (order_id, item['product_id'], item['quantity'], item['price'])
            )
        connection.commit()

        # Simulate processing delay
        time.sleep(5)

        # Update order status to "PENDING"
        cursor.execute("UPDATE `Order` SET status = 'PENDING' WHERE order_id = %s", (order_id,))
        connection.commit()

        # Send callback with updated status if provided
        if callback_url:
            try:
                requests.post(callback_url, json={'order_id': order_id, 'status': 'PENDING'})
            except requests.exceptions.RequestException as e:
                print(f"Callback failed: {e}")

    except mysql.connector.Error as err:
        connection.rollback()
        print(f"Error processing order: {err}")
    finally:
        cursor.close()
        connection.close()

@order_blueprint.route('/callback/<int:order_id>/status', methods=['GET', 'POST'])
def check_order_status(order_id):
    """Endpoint to check the status of an order."""
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("SELECT status FROM `Order` WHERE order_id = %s", (order_id,))
    result = cursor.fetchone()

    cursor.close()
    connection.close()

    if result:
        return jsonify({"order_id": order_id, "status": result['status']}), 200
    else:
        return jsonify({'error': 'Order not found'}), 404