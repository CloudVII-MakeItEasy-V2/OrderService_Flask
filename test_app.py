import pytest
from app import app as flask_app
from routes import get_db_connection

# Configure the test database
flask_app.config['MYSQL_HOST'] = 'localhost'
flask_app.config['MYSQL_USER'] = 'root'
flask_app.config['MYSQL_PASSWORD'] = 'dbuserdbuser'
flask_app.config['MYSQL_DB'] = 'p2_database'  # Use a separate test database

@pytest.fixture
def app():
    yield flask_app  # This fixture is required by pytest-flask to access the app


@pytest.fixture
def setup_test_database():
    # Set up the test database with necessary tables and data
    connection = get_db_connection()
    cursor = connection.cursor()

    # Disable foreign key checks to avoid constraint issues during table drop
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("DROP TABLE IF EXISTS order_item")
    cursor.execute("DROP TABLE IF EXISTS orders")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    # Recreate tables for testing
    cursor.execute("""
        CREATE TABLE orders (
            id INT AUTO_INCREMENT PRIMARY KEY,
            customer_id INT NOT NULL,
            status VARCHAR(20) DEFAULT 'PENDING'
        )
    """)

    cursor.execute("""
        CREATE TABLE order_item (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            product_name VARCHAR(255),
            quantity INT,
            FOREIGN KEY (order_id) REFERENCES orders(id)
        )
    """)

    # Insert test data if needed
    cursor.execute("INSERT INTO orders (customer_id, status) VALUES (1, 'PENDING')")
    connection.commit()

    cursor.close()
    connection.close()
    
    # Yield to indicate the setup phase is complete
    yield

    # Teardown: Drop tables after tests
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("DROP TABLE IF EXISTS order_item")
    cursor.execute("DROP TABLE IF EXISTS orders")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
    connection.commit()
    cursor.close()
    connection.close()

def test_get_orders(client, setup_test_database):
    # Test the GET /orders endpoint
    response = client.get('/orders')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)  # Check that we get a list of orders
    assert len(data) > 0  # Ensure there’s at least one order in the response
    assert data[0]['status'] == 'PENDING'  # Check a specific field in the first order


def test_create_order(client, setup_test_database):
    # Test the POST /orders endpoint
    new_order = {
        'customer_id': 2,
        'status': 'NEW'
    }
    response = client.post('/orders', json=new_order)
    assert response.status_code == 201
    data = response.get_json()
    assert data['customer_id'] == new_order['customer_id']
    assert data['status'] == new_order['status']
    assert 'id' in data  # Check that the ID of the new order is returned

