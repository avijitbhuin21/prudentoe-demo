from flask import Flask, render_template, request, jsonify
import razorpay
import hmac
import hashlib
import logging
import traceback
from datetime import datetime
from supabase import create_client, Client

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('payment_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Payment Configuration
PAYMENT_AMOUNT_RS = 1  # Change this to 99 for production
PAYMENT_AMOUNT_PAISE = PAYMENT_AMOUNT_RS * 100  # Convert to paise

# Razorpay Live credentials
# RAZORPAY_KEY_ID = 'rzp_live_nk5TusXDwjy8pV'
# RAZORPAY_KEY_SECRET = 'vb9oKZSRYyM2BkAG4vdKsT2A'

# Razorpay Test credentials (for fallback testing)
RAZORPAY_KEY_ID = 'rzp_test_mTfoYlS40taGLb'
RAZORPAY_KEY_SECRET = '4PNcnzuY2KzAda8ar45Cahwn'

# Supabase credentials
SUPABASE_URL = 'https://ojslpvgxujrixjwqcvag.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9qc2xwdmd4dWpyaXhqd3FjdmFnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDkzMTkyMDIsImV4cCI6MjA2NDg5NTIwMn0.PBcusMoKw1r33gwkZ5RuM98QTUdBM6Zv9gY8WhWOLXg'

# Initialize clients
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Helper function to save booking data to Supabase
def save_booking_to_db(booking_data, payment_status):
    try:
        # Only save if phone or email is present
        if not booking_data.get('phone') and not booking_data.get('email'):
            return None
            
        data = {
            'name': booking_data.get('name'),
            'email': booking_data.get('email'),
            'phone_number': booking_data.get('phone'),
            'address_line_1': booking_data.get('address1'),
            'address_line_2': booking_data.get('address2'),
            'zip_code': booking_data.get('zipCode'),
            'contact_method': booking_data.get('preferredContact'),
            'selected_date': booking_data.get('date'),
            'selected_time': booking_data.get('time'),
            'payment_status': payment_status
        }
        
        result = supabase.table('booking').insert(data).execute()
        return result.data[0] if result.data else None
        
    except Exception as e:
        print(f"Error saving to database: {str(e)}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create-order', methods=['POST'])
def create_order():
    try:
        # Get booking data from request
        data = request.get_json()
        
        logger.info(f"Creating order for customer: {data.get('name', 'Unknown')}")
        logger.info(f"Payment amount: Rs. {PAYMENT_AMOUNT_RS} ({PAYMENT_AMOUNT_PAISE} paise)")
        
        # Create Razorpay order
        order_data = {
            'amount': PAYMENT_AMOUNT_PAISE,  # Amount in paise
            'currency': 'INR',
            'receipt': f"bk_{data.get('phone', 'unk')[:8]}_{datetime.now().strftime('%m%d_%H%M%S')}",
            'notes': {
                'name': data.get('name'),
                'email': data.get('email'),
                'phone': data.get('phone'),
                'address1': data.get('address1'),
                'address2': data.get('address2'),
                'zipCode': data.get('zipCode'),
                'preferredContact': data.get('preferredContact'),
                'date': data.get('date'),
                'time': data.get('time'),
                'booking_type': 'dental_appointment'
            }
        }
        
        logger.info(f"Order data: {order_data}")
        
        # Create order with Razorpay
        order = razorpay_client.order.create(data=order_data)
        
        logger.info(f"Order created successfully: {order['id']}")
        logger.info(f"Order details: {order}")
        
        return jsonify({
            'success': True,
            'order_id': order['id'],
            'amount': order['amount'],
            'currency': order['currency'],
            'key_id': RAZORPAY_KEY_ID
        })
        
    except Exception as e:
        logger.error(f"Error creating order: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    try:
        # Get payment data from request
        data = request.get_json()
        
        logger.info("Payment verification started")
        logger.info(f"Received data: {data}")
        
        # Verify payment signature
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')
        booking_data = data.get('booking_data', {})
        
        logger.info(f"Order ID: {razorpay_order_id}")
        logger.info(f"Payment ID: {razorpay_payment_id}")
        logger.info(f"Signature: {razorpay_signature}")
        
        # Create signature for verification
        body = razorpay_order_id + "|" + razorpay_payment_id
        expected_signature = hmac.new(
            key=RAZORPAY_KEY_SECRET.encode(),
            msg=body.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()
        
        logger.info(f"Expected signature: {expected_signature}")
        logger.info(f"Received signature: {razorpay_signature}")
        
        if hmac.compare_digest(expected_signature, razorpay_signature):
            logger.info("Payment signature verified successfully")
            
            # Payment is verified - save to database with success status
            db_result = save_booking_to_db(booking_data, 'success')
            
            logger.info(f"Booking saved to database: {db_result}")
            
            return jsonify({
                'success': True,
                'message': 'Payment verified successfully',
                'booking_id': db_result.get('id') if db_result else None
            })
        else:
            logger.error("Payment signature verification failed")
            
            # Payment verification failed - save to database with failed status
            save_booking_to_db(booking_data, 'failed')
            
            return jsonify({
                'success': False,
                'error': 'Payment verification failed'
            }), 400
            
    except Exception as e:
        logger.error(f"Error in payment verification: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/payment-cancelled', methods=['POST'])
def payment_cancelled():
    try:
        # Get booking data from request
        data = request.get_json()
        booking_data = data.get('booking_data', {})
        
        logger.info(f"Payment cancelled for customer: {booking_data.get('name', 'Unknown')}")
        
        # Save to database with cancelled status
        db_result = save_booking_to_db(booking_data, 'cancelled')
        
        logger.info(f"Cancellation recorded: {db_result}")
        
        return jsonify({
            'success': True,
            'message': 'Payment cancellation recorded',
            'booking_id': db_result.get('id') if db_result else None
        })
        
    except Exception as e:
        logger.error(f"Error recording cancellation: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/payment-failed', methods=['POST'])
def payment_failed():
    try:
        # Get booking data from request
        data = request.get_json()
        booking_data = data.get('booking_data', {})
        error_details = data.get('error_details', {})
        
        logger.error("=== PAYMENT FAILURE DETAILS ===")
        logger.error(f"Customer: {booking_data.get('name', 'Unknown')}")
        logger.error(f"Phone: {booking_data.get('phone', 'Unknown')}")
        logger.error(f"Email: {booking_data.get('email', 'Unknown')}")
        logger.error(f"Timestamp: {data.get('timestamp', 'Unknown')}")
        logger.error(f"User Agent: {data.get('user_agent', 'Unknown')}")
        logger.error(f"URL: {data.get('url', 'Unknown')}")
        
        if error_details:
            logger.error(f"Error Code: {error_details.get('code', 'Unknown')}")
            logger.error(f"Error Description: {error_details.get('description', 'Unknown')}")
            logger.error(f"Error Source: {error_details.get('source', 'Unknown')}")
            logger.error(f"Error Step: {error_details.get('step', 'Unknown')}")
            logger.error(f"Error Reason: {error_details.get('reason', 'Unknown')}")
            logger.error(f"Error Metadata: {error_details.get('metadata', 'Unknown')}")
            logger.error(f"Error Field: {error_details.get('field', 'Unknown')}")
        
        logger.error("===============================")
        
        # Save to database with failed status
        db_result = save_booking_to_db(booking_data, 'failed')
        
        logger.info(f"Payment failure recorded: {db_result}")
        
        return jsonify({
            'success': True,
            'message': 'Payment failure recorded',
            'booking_id': db_result.get('id') if db_result else None
        })
        
    except Exception as e:
        logger.error(f"Error recording payment failure: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/config', methods=['GET'])
def get_config():
    """Endpoint to check current payment configuration"""
    return jsonify({
        'payment_amount_rs': PAYMENT_AMOUNT_RS,
        'payment_amount_paise': PAYMENT_AMOUNT_PAISE,
        'razorpay_key_id': RAZORPAY_KEY_ID,
        'environment': 'live' if 'live' in RAZORPAY_KEY_ID else 'test'
    })

@app.route('/test-razorpay', methods=['GET'])
def test_razorpay():
    """Test Razorpay connection"""
    try:
        # Try to fetch a dummy order to test credentials
        logger.info("Testing Razorpay connection...")
        
        # Create a test order
        test_order_data = {
            'amount': 100,  # Rs. 1
            'currency': 'INR',
            'receipt': f"test_{datetime.now().strftime('%m%d_%H%M%S')}"
        }
        
        order = razorpay_client.order.create(data=test_order_data)
        logger.info(f"Test order created: {order['id']}")
        
        return jsonify({
            'success': True,
            'message': 'Razorpay connection successful',
            'test_order_id': order['id'],
            'credentials_valid': True
        })
        
    except Exception as e:
        logger.error(f"Razorpay connection test failed: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'credentials_valid': False
        }), 500

@app.route('/diagnose-payment', methods=['GET'])
def diagnose_payment():
    """Diagnose common payment issues"""
    try:
        diagnostics = {
            'razorpay_key_id': RAZORPAY_KEY_ID,
            'environment': 'live' if 'live' in RAZORPAY_KEY_ID else 'test',
            'payment_amount_rs': PAYMENT_AMOUNT_RS,
            'payment_amount_paise': PAYMENT_AMOUNT_PAISE,
            'dpa_compliance_issue': {
                'error': 'DPA entity data not found',
                'description': 'This error indicates your live Razorpay account needs DPA compliance setup',
                'solution': 'Contact Razorpay support to complete DPA compliance registration',
                'steps': [
                    '1. Login to Razorpay Dashboard',
                    '2. Go to Account & Settings → Compliance',
                    '3. Complete DPA (Data Protection Act) registration',
                    '4. Submit required business documents',
                    '5. Wait for Razorpay approval (usually 2-3 business days)'
                ]
            },
            'issues_to_check': [
                'DPA compliance registration (CRITICAL for live accounts)',
                'Account activation status',
                'Payment methods enabled',
                'Settlement account configured',
                'KYC completion status',
                'Website/app domain whitelisting'
            ],
            'common_live_issues': {
                'dpa_error': 'DPA entity data not found - compliance not completed',
                'auto_failure': 'Live accounts need proper KYC and settlement setup',
                'no_payment_methods': 'Payment methods (cards, wallets, UPI) need to be enabled',
                'domain_restriction': 'Domain needs to be whitelisted in Razorpay dashboard',
                'settlement_account': 'Bank account needs to be verified for settlements'
            }
        }
        
        # Test basic connection
        try:
            test_order = razorpay_client.order.create({
                'amount': 100,
                'currency': 'INR',
                'receipt': f"diag_{datetime.now().strftime('%m%d_%H%M%S')}"
            })
            diagnostics['connection_test'] = 'SUCCESS'
            diagnostics['test_order_id'] = test_order['id']
        except Exception as e:
            diagnostics['connection_test'] = 'FAILED'
            diagnostics['connection_error'] = str(e)
            
            # Check for specific DPA error
            if 'DPA entity data not found' in str(e):
                diagnostics['dpa_error_detected'] = True
                diagnostics['immediate_action_required'] = 'Complete DPA compliance in Razorpay Dashboard'
        
        return jsonify(diagnostics)
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'message': 'Diagnostic failed'
        }), 500

@app.route('/dpa-compliance-guide', methods=['GET'])
def dpa_compliance_guide():
    """Provide detailed DPA compliance guide"""
    return jsonify({
        'title': 'Razorpay DPA Compliance Setup Guide',
        'error_explanation': {
            'error': 'DPA entity data not found for the given clientId or dpaid',
            'meaning': 'Your live Razorpay account has not completed Data Protection Act compliance registration',
            'impact': 'Live payments will fail until DPA compliance is completed'
        },
        'solution_steps': [
            {
                'step': 1,
                'title': 'Login to Razorpay Dashboard',
                'action': 'Go to https://dashboard.razorpay.com and login with your account'
            },
            {
                'step': 2,
                'title': 'Navigate to Compliance Section',
                'action': 'Go to Account & Settings → Compliance → DPA Registration'
            },
            {
                'step': 3,
                'title': 'Complete DPA Registration Form',
                'action': 'Fill out all required business and compliance information'
            },
            {
                'step': 4,
                'title': 'Submit Required Documents',
                'action': 'Upload business registration, PAN, GST, and other required documents'
            },
            {
                'step': 5,
                'title': 'Wait for Approval',
                'action': 'Razorpay will review and approve (usually 2-3 business days)'
            },
            {
                'step': 6,
                'title': 'Test Live Payments',
                'action': 'Once approved, test live payments again'
            }
        ],
        'contact_support': {
            'email': 'support@razorpay.com',
            'phone': '+91-80-61606161',
            'message': 'Mention DPA compliance issue and provide your account details'
        },
        'temporary_solution': {
            'option': 'Use test mode for development',
            'note': 'Switch to test credentials while waiting for DPA approval'
        }
    })

if __name__ == '__main__':
    logger.info(f"Starting Flask app with payment amount: Rs. {PAYMENT_AMOUNT_RS}")
    logger.info(f"Razorpay Key ID: {RAZORPAY_KEY_ID}")
    logger.info(f"Environment: {'LIVE' if 'live' in RAZORPAY_KEY_ID else 'TEST'}")
    logger.info("=== IMPORTANT LIVE ACCOUNT CHECKLIST ===")
    logger.info("1. Ensure KYC is completed in Razorpay Dashboard")
    logger.info("2. Verify settlement bank account is added and verified")
    logger.info("3. Check if payment methods (cards, wallets, UPI) are enabled")
    logger.info("4. Confirm domain is whitelisted in Razorpay settings")
    logger.info("5. Check account activation status")
    logger.info("=========================================")
    app.run(debug=True, host='0.0.0.0', port=5000)