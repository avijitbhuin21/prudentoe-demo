from flask import Flask, render_template, request, jsonify
import razorpay
import hmac
import hashlib
import logging
import traceback
import datetime
import os
from supabase import create_client, Client
from dotenv import load_dotenv
import json

load_dotenv()


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
PAYMENT_AMOUNT_RS = 99  # Payment amount for dental appointment booking
PAYMENT_AMOUNT_PAISE = PAYMENT_AMOUNT_RS * 100  # Convert to paise

RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID','') if os.getenv('RAZORPAY_KEY_ID','') != '' else 'rzp_test_mTfoYlS40taGLb'
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET','') if os.getenv('RAZORPAY_KEY_SECRET','') != '' else '4PNcnzuY2KzAda8ar45Cahwn'

# Supabase credentials
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://ojslpvgxujrixjwqcvag.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9qc2xwdmd4dWpyaXhqd3FjdmFnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDkzMTkyMDIsImV4cCI6MjA2NDg5NTIwMn0.PBcusMoKw1r33gwkZ5RuM98QTUdBM6Zv9gY8WhWOLXg')

# Initialize clients
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def generate_time_slots(
    num_days: int = 15,
    start_time_str: str = "10:00",
    end_time_str: str = "20:00",
    slot_duration_mins: int = 45
) -> list[dict]:
    today = datetime.date.today()
    start_date = today + datetime.timedelta(days=1)
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    try:
        start_time = datetime.datetime.strptime(start_time_str, '%H:%M').time()
        end_time = datetime.datetime.strptime(end_time_str, '%H:%M').time()
    except ValueError:
        raise ValueError("Invalid time format. Please use 'HH:MM'.")
    all_days_slots = []
    for i in range(num_days):
        current_date = start_date + datetime.timedelta(days=i)
        day_of_week = day_names[current_date.weekday()] 
        time_slots_for_day = []
        current_slot_time = datetime.datetime.combine(current_date, start_time)
        end_datetime = datetime.datetime.combine(current_date, end_time)
        while current_slot_time < end_datetime:
            time_slots_for_day.append(current_slot_time.strftime('%H:%M'))
            current_slot_time += datetime.timedelta(minutes=slot_duration_mins)
        day_data = {
            "date": current_date.strftime('%Y-%m-%d'), 
            "day": day_of_week,
            "time_slots": time_slots_for_day
        }
        all_days_slots.append(day_data)
    return all_days_slots

def get_formatted_booked_slots(supabase_client: Client) -> list[dict]:
    try:
        response = supabase_client.table('booking').select('selected_date, selected_time, payment_status').eq('payment_status', 'success').execute()
        if not response.data:
            return []
        booked_slots_by_date = {}
        for booking in response.data:
            date_str = booking.get('selected_date')
            time_str = booking.get('selected_time')
            if date_str and time_str:
                try:
                    datetime.datetime.strptime(date_str, '%Y-%m-%d')
                    if date_str not in booked_slots_by_date:
                        booked_slots_by_date[date_str] = []
                    booked_slots_by_date[date_str].append(time_str)
                except ValueError:
                    print(f"Warning: Invalid date format '{date_str}' in booking data. Skipping entry.")
                    continue
        formatted_data = []
        sorted_dates = sorted(booked_slots_by_date.keys())
        for date_str in sorted_dates:
            times = sorted(list(set(booked_slots_by_date[date_str])))
            try:
                date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                day_name = date_obj.strftime('%A')  
                formatted_data.append({
                    "date": date_str,
                    "day": day_name,
                    "time_slots": times
                })
            except ValueError:
                print(f"Warning: Could not parse date '{date_str}' during final formatting. Skipping this date.")
                continue
        return formatted_data
    except Exception as e:
        print(f"An error occurred while fetching or processing booked slots: {e}")
        return []

def get_available_slots(generated_slots: list[dict] = generate_time_slots(), booked_slots: list[dict] = get_formatted_booked_slots(supabase)) -> list[dict]:
    available_slots_result = []
    booked_slots_map = {slot_info['date']: slot_info.get('time_slots', []) for slot_info in booked_slots}
    for day_data in generated_slots:
        date_str = day_data['date']
        day_name = day_data['day']
        all_time_slots_for_day = day_data.get('time_slots', [])
        booked_time_slots_for_day = booked_slots_map.get(date_str, [])
        all_unavailable_slots = set()
        if booked_time_slots_for_day:
            for booked_slot in booked_time_slots_for_day:
                try:
                    idx = all_time_slots_for_day.index(booked_slot)
                    start_index = max(0, idx - 2)
                    end_index = min(len(all_time_slots_for_day), idx + 3)
                    for i in range(start_index, end_index):
                        all_unavailable_slots.add(all_time_slots_for_day[i])
                except ValueError:
                    print(f"Warning: Booked slot {booked_slot} on {date_str} not found in generated slots. It will be ignored for buffer calculation.")
                    all_unavailable_slots.add(booked_slot) 
        current_available_slots = [
            slot for slot in all_time_slots_for_day 
            if slot not in all_unavailable_slots
        ]
        if current_available_slots:
            available_slots_result.append({
                "date": date_str,
                "day": day_name,
                "time_slots": current_available_slots
            })
    return available_slots_result


# Helper function to save booking data to Supabase
def save_booking_to_db(booking_data, payment_status):
    try:
        # Only save if phone or email is present
        if not booking_data.get('phone') and not booking_data.get('email'):
            return None
            
        data = {
            'name': booking_data.get('name') or None,
            'email': booking_data.get('email') or None,
            'phone_number': booking_data.get('phone') or None,
            'address': booking_data.get('address1') or None,
            'zipcode': booking_data.get('zipCode') or None,
            'contact_method': booking_data.get('preferredContact') or None,
            'selected_date': booking_data.get('date') or None,
            'selected_time': booking_data.get('time') or None,
            'payment_status': payment_status,
            'utm_data': booking_data.get('utm_data') or {}
        }
        
        result = supabase.table('booking').insert(data).execute()
        return result.data[0] if result.data else None
        
    except Exception as e:
        print(f"Error saving to database: {str(e)}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/thank-you')
def thank_you():
    return render_template('thank-you.html')

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
            'receipt': f"bk_{data.get('phone', 'unk')[:8]}_{datetime.datetime.now().strftime('%m%d_%H%M%S')}",
            'notes': {
                'name': data.get('name'),
                'email': data.get('email'),
                'phone': data.get('phone'),
                'address': data.get('address1'),  # Updated field name
                'zipcode': data.get('zipCode'),   # Updated field name
                'contact_method': data.get('preferredContact'),  # Updated field name
                'selected_date': data.get('date'),  # Updated field name
                'selected_time': data.get('time'),  # Updated field name
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

@app.route('/save-non-serviceable', methods=['POST'])
def save_non_serviceable():
    try:
        # Get booking data from request
        data = request.get_json()
        booking_data = data.get('booking_data', {})
        
        logger.info(f"Saving non-serviceable booking for: {booking_data.get('name', 'Unknown')}")
        logger.info(f"Zipcode: {booking_data.get('zipCode', 'Unknown')}")
        
        # Save to database with area_not_serviceable status
        db_result = save_booking_to_db(booking_data, 'area_not_serviceable')
        
        logger.info(f"Non-serviceable booking saved: {db_result}")
        
        return jsonify({
            'success': True,
            'message': 'Non-serviceable booking recorded',
            'booking_id': db_result.get('id') if db_result else None
        })
        
    except Exception as e:
        logger.error(f"Error saving non-serviceable booking: {str(e)}")
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
            'receipt': f"test_{datetime.datetime.now().strftime('%m%d_%H%M%S')}"
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
                'receipt': f"diag_{datetime.datetime.now().strftime('%m%d_%H%M%S')}"
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

@app.route('/get-available-slots', methods=['GET'])
def get_available_slots_endpoint():
    """Endpoint to get available time slots for booking"""
    try:
        # Generate time slots
        generated_slots = generate_time_slots()
        print(f"Generated Slots: {json.dumps(generated_slots, indent=2)}")
        
        # Get booked slots from database
        booked_slots = get_formatted_booked_slots(supabase)
        print(f"Booked Slots: {json.dumps(booked_slots, indent=2)}")
        
        # Get available slots by comparing generated and booked slots
        available_slots = get_available_slots(generated_slots, booked_slots)
        print(f"Available Slots: {json.dumps(available_slots, indent=2)}")
        
        return jsonify({
            'success': True,
            'available_slots': available_slots
        })
        
    except Exception as e:
        logger.error(f"Error fetching available slots: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
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