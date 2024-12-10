from flask import Blueprint, jsonify, request, url_for, make_response
import mysql.connector
from mysql.connector import Error
from datetime import datetime
from threading import Thread
import requests
import time
import os
import boto3  # Using AWS SDK without explicit keys

MYSQL_HOST = "makeiteasy.ck0scewemjwp.us-east-1.rds.amazonaws.com"
MYSQL_USER = "root"
MYSQL_PASSWORD = "dbuserdbuser"
MYSQL_DB = "Order_Service"

order_blueprint = Blueprint('order_blueprint', __name__)

seller_service_url = os.getenv('MICROSERVICE3_SELLER_SERVICE_URL', 'http://host.docker.internal:8000')

# We rely on the default AWS credential provider chain now
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
SNS_TOPIC_ARN = os.getenv('SNS_TOPIC_ARN')  # ARN of your SNS topic

# Initialize SNS client without explicitly passing credentials
sns_client = None
if SNS_TOPIC_ARN:
    sns_client = boto3.client(
        'sns',
        region_name=AWS_REGION
    )

def get_db_connection():
    connection = mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        autocommit=False
    )
    return connection

@order_blueprint.route('/', methods=['GET'])
def home():
    return "Welcome to the Orders API", 200

@order_blueprint.route('/test_db_connection', methods=['GET'])
def test_db_connection():
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE();")
        database_name = cursor.fetchone()[0]
        cursor.close()
        connection.close()
        return jsonify({"status": "success", "database": database_name}), 200
    except Error as e:
        return jsonify({"status": "failure", "error": str(e)}), 500

def check_and_update_stock_preorder(items):
    update_stock_payload = {"items": []}
    for item in items:
        product_id = item['product_id']
        quantity = item['quantity']

        product_resp = requests.get(f"{seller_service_url}/product/{product_id}")
        if product_resp.status_code != 200:
            return False, f"Failed to fetch product {product_id} details"
        product_data = product_resp.json()
        current_stock = product_data['stock']

        if current_stock < quantity:
            return False, f"Not enough quantity for product {product_id}. Available: {current_stock}, Requested: {quantity}"

        update_stock_payload["items"].append({"product_id": product_id, "quantity": quantity})
    return True, update_stock_payload

def finalize_stock_update(update_stock_payload):
    resp = requests.post(f"{seller_service_url}/product/update_stock", json=update_stock_payload)
    if resp.status_code == 200:
        return True, None
    else:
        return False, f"Failed to update stock: {resp.text}"

def publish_order_event(order_id):
    """
    Publish event to SNS for service choreography.
    """
    if sns_client and SNS_TOPIC_ARN:
        message = {
            "event_type": "order_created",
            "order_id": order_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        import json
        message_str = json.dumps(message)
        try:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=message_str,
                MessageGroupId="orderEvents"  # If using FIFO topics
            )
        except Exception as e:
            print(f"Failed to publish event to SNS: {e}")
    else:
        print("SNS client not configured or no SNS_TOPIC_ARN. Cannot publish event.")

@order_blueprint.route('/orders', methods=['GET'])
def get_orders():
    page = request.args.get('page', default=1, type=int)
    page_size = request.args.get('page_size', default=10, type=int)
    customer_id = request.args.get('customer_id', type=int)

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        query = "SELECT * FROM `Order`"
        params = []
        if customer_id:
            query += " WHERE customer_id = %s"
            params.append(customer_id)
        query += " LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])

        cursor.execute(query, tuple(params))
        orders = cursor.fetchall()

        for order in orders:
            cursor.execute("SELECT * FROM OrderItem WHERE order_id = %s", (order['order_id'],))
            order_items = cursor.fetchall()
            order['items'] = order_items
            if 'total_amount' in order and order['total_amount'] is not None:
                order['total_amount'] = str(order['total_amount'])

        orders_response = {
            "orders": orders,
            "_links": {
                "self": url_for('order_blueprint.get_orders', page=page, page_size=page_size, customer_id=customer_id, _external=True),
                "next": url_for('order_blueprint.get_orders', page=page + 1, page_size=page_size, customer_id=customer_id, _external=True)
            }
        }

        response = make_response(jsonify(orders_response), 200)
        next_url = url_for('order_blueprint.get_orders', page=page + 1, page_size=page_size, customer_id=customer_id, _external=True)
        response.headers['Link'] = f'<{next_url}>; rel="next"'
    except mysql.connector.Error as err:
        response = jsonify({"error": str(err)})
        response.status_code = 500
    finally:
        cursor.close()
        connection.close()

    return response

@order_blueprint.route('/create_order', methods=['POST'])
def create_order():
    data = request.json
    customer_id = data['customer_id']
    status = data.get('status', 'Pending')
    tracking_number = data.get('tracking_number', None)
    items = data.get('items', [])
    total_amount = sum(item['price'] * item['quantity'] for item in items)
    created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    stock_ok, stock_result = check_and_update_stock_preorder(items)
    if not stock_ok:
        return jsonify({"error": stock_result}), 400

    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        # Insert order
        cursor.execute("""
            INSERT INTO `Order` (customer_id, total_amount, status, tracking_number, created_date)
            VALUES (%s, %s, %s, %s, %s)
        """, (customer_id, total_amount, status, tracking_number, created_date))

        order_id = cursor.lastrowid

        for item in items:
            cursor.execute("""
                INSERT INTO OrderItem (order_id, product_id, quantity, price)
                VALUES (%s, %s, %s, %s)
            """, (order_id, item['product_id'], item['quantity'], item['price']))

        stock_updated, stock_err = finalize_stock_update(stock_result)
        if not stock_updated:
            connection.rollback()
            return jsonify({"error": stock_err}), 500

        connection.commit()

        # Publish event after successful order creation
        publish_order_event(order_id)

        response_data = {
            'order_id': order_id,
            'customer_id': customer_id,
            'total_amount': str(total_amount),
            'status': status,
            'tracking_number': tracking_number,
            'items': items,
            '_links': {
                "self": url_for('order_blueprint.get_order', order_id=order_id, _external=True)
            }
        }

        response = make_response(jsonify(response_data), 201)
        response.headers['Link'] = url_for('order_blueprint.get_order', order_id=order_id, _external=True)
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
        cursor.execute("SELECT * FROM `Order` WHERE order_id = %s", (order_id,))
        order = cursor.fetchone()

        if not order:
            return jsonify({"error": "Order not found"}), 404

        cursor.execute("SELECT * FROM OrderItem WHERE order_id = %s", (order_id,))
        items = cursor.fetchall()

        if 'total_amount' in order and order['total_amount'] is not None:
            order['total_amount'] = str(order['total_amount'])

        order['items'] = items

        order_response = {
            "order_id": order['order_id'],
            "customer_id": order['customer_id'],
            "total_amount": order['total_amount'],
            "status": order['status'],
            "tracking_number": order['tracking_number'],
            "created_date": str(order['created_date']) if order['created_date'] else None,
            "items": order['items'],
            "_links": {
                "self": url_for('order_blueprint.get_order', order_id=order['order_id'], _external=True)
            }
        }

        return jsonify(order_response)

    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500
    finally:
        cursor.close()
        connection.close()

@order_blueprint.route('/create_order/async', methods=['POST'])
def create_order_async():
    data = request.json
    customer_id = data.get('customer_id')
    status = data.get('status', "Processing")
    items = data.get('items', [])
    total_amount = sum(item['price'] * item['quantity'] for item in items)
    callback_url = data.get('callback_url')
    created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    stock_ok, stock_result = check_and_update_stock_preorder(items)
    if not stock_ok:
        return jsonify({"error": stock_result}), 400

    thread = Thread(target=process_order_async, args=(customer_id, status, items, total_amount, callback_url, created_date, stock_result))
    thread.start()

    return jsonify({'status': 'Order is being processed'}), 202

def process_order_async(customer_id, status, items, total_amount, callback_url, created_date, stock_result):
    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            "INSERT INTO `Order` (customer_id, total_amount, status, created_date) VALUES (%s, %s, %s, %s)",
            (customer_id, total_amount, "PROCESSING", created_date)
        )
        order_id = cursor.lastrowid

        for item in items:
            cursor.execute(
                "INSERT INTO OrderItem (order_id, product_id, quantity, price) VALUES (%s, %s, %s, %s)",
                (order_id, item['product_id'], item['quantity'], item['price'])
            )

        stock_updated, stock_err = finalize_stock_update(stock_result)
        if not stock_updated:
            connection.rollback()
            print(f"Async order {order_id} stock update failed: {stock_err}")
            return

        connection.commit()

        # Simulate processing delay
        time.sleep(5)

        cursor.execute("UPDATE `Order` SET status = 'PENDING' WHERE order_id = %s", (order_id,))
        connection.commit()

        # Publish event after successful async order creation
        publish_order_event(order_id)

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
    
@order_blueprint.route('/orders/<int:order_id>/track', methods=['GET'])
def track_order(order_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # Retrieve the order's tracking number
        cursor.execute("SELECT tracking_number FROM `Order` WHERE order_id = %s", (order_id,))
        order = cursor.fetchone()

        if not order or not order['tracking_number']:
            return jsonify({"error": "Order not found or tracking number unavailable"}), 404

        tracking_number = order['tracking_number']
        tracking_url = f"https://www.trackingmore.com/track/en/{tracking_number}?express=ups"

        return jsonify({
            "order_id": order_id,
            "tracking_number": tracking_number,
            "tracking_url": tracking_url
        }), 200

    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500
    finally:
        cursor.close()
        connection.close()

