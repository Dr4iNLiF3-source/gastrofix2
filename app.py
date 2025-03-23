from flask import Flask, request, jsonify, make_response, redirect, url_for, render_template, send_file
import jwt
import datetime
from threading import Thread, Timer
import requests
import time
import os
import json
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import re

#########################################################################################################################
###     CONFIGURAZIONE FLASK     ########################################################################################
#########################################################################################################################

SECRET_KEY = 'your_secret_key'
app = Flask(__name__)



#########################################################################################################################
###     VARIABILI GLOBALI     ##########################################################################################
#########################################################################################################################

users_sessions = {}  # Chiave: username, valore: sessione di requests (Session) e stato del login
process_active = {}  # Chiave: username, valore: stato del processo (True/False)
files_directory = 'files'  # Cartella principale dove vengono salvati i file



#########################################################################################################################
###     CLASSE USERPROCESS     ##########################################################################################
#########################################################################################################################

class UserProcess:
    def __init__(self, username, session, month, year):
        self.username = username
        self.session = session
        self.process_thread = None
        self.dummy_document = "dummy.xlsx"
        self.month = month
        self.year = year
        self.locations = []
        self.articles = []
        self.month = month
        self.year = year

    def run_process(self):
        """Funzione che esegue le operazioni usando la sessione dell'utente."""
        global process_active
        process_active[self.username] = "Started"

        # Simula operazioni continue con la sessione di Gastrofix
        print(f"Processo avviato per l'utente {self.username}")
        process_active[self.username] = "Generating reports..."
        self.get_locations(self.month,self.year)
        self.articles = self.process_locations_multithreaded(self.locations)
        self.make_document(self.articles)

        # Aggiorna la lista dei file e lo stato del processo
        process_active.pop(self.username)

    def get_turnover_location(self, month, day, year):
        url='https://no.gastrofix.com/icash/report/nr/Payment/html/jr'
        data= {
            "period":{"from":f"{year}-{month}-{day}","to":f"{year}-{month}-{day}"},
            "filters":{}
            }
        response = self.session.post(url, data=json.dumps(data), headers={'Content-Type': 'application/json;charset=utf-8'})
        location = response.json()['payload']['location']
        return location

    def get_cards_payments_location(self,month, day , year):
        url='https://no.gastrofix.com/icash/report/nr/Payment_CreditCard/html/jr'
        data= {
            "period":{"from":f"{year}-{month}-{day}","to":f"{year}-{month}-{day}"},
            "filters":{}
            }
        response = self.session.post(url, data=json.dumps(data), headers={'Content-Type': 'application/json;charset=utf-8'})
        location = response.json()['payload']['location']
        return location


    def get_groups_location(self,month, day, year):
        url='https://no.gastrofix.com/icash/report/nr/Order_ArticleSuperGroup/html/jr'
        data= {
            "period":{"from":f"{year}-{month}-{day}","to":f"{year}-{month}-{day}"},
            "filters":{}
            }
        response = self.session.post(url, data=json.dumps(data), headers={'Content-Type': 'application/json;charset=utf-8'})
        location = response.json()['payload']['location']
        return location

    def get_locations(self,month, year):
        month = str(month).zfill(2)
        try:
            for days in range(1, 32):
                day = str(days).zfill(2)
                print(f"Processing Day {day}")
                process_active[self.username] = f"Processing Day {day}"
                a=self.get_turnover_location(month, day, year)
                b=self.get_cards_payments_location(month, day, year)
                c=self.get_groups_location(month, day, year)
                self.locations.append({'day':day, 'month':month, 'year':year, 'turnover':a, 'cards':b, 'groups':c})
            return
        except Exception as e:
            pass

    def get_turnover(self, location, month, day, year):
        try:
            turnover_response=self.session.get(location)
            soup = BeautifulSoup(turnover_response.text, "html.parser")

            # Find the 'Total' value (final total at the bottom)
            total_element = soup.find("span", string="Turnover")
            if not total_element:
                return False, False, False
            
            not_turnover = soup.find("span", string="Non-Turnover")

            # Extract 'App Order' total and tip
            app_order_row = soup.find("span", string="App Order")
            app_order_tip = app_order_row.find_next("span").find_next("span").text if app_order_row else ""
            app_order_total=app_order_row.find_next("span").find_next("span").find_next("span").text if app_order_row else ""

            # Extract 'Kort' or 'Kredittkort' total and tip
            kort_row = soup.find("span", string="Kort") or soup.find("span", string="Kredittkort")
            kort_tip = kort_row.find_next("span").find_next("span").text if kort_row else ""
            kort_total=kort_row.find_next("span").find_next("span").find_next("span").text if kort_row else ""

            app={'app_order_tip':app_order_tip, 'app_order_total':app_order_total}
            kort={'kort_tip':kort_tip, 'kort_total':kort_total}

            if not_turnover:
                a=not_turnover.find_all_next("tr")
                for tr in a[:-2]:
                    if tr.find("span", string="Faktura"):
                        response=self.session.get(f"https://no.gastrofix.com/icash/report/jr/Eventim/html?date=8&filter=0&type=13&format=&from={year}-{month}-{day}&to={year}-{month}-{day}")
                        faktura_location = response.json()['payload']['location']
                        faktura_response=self.session.get(faktura_location)
                        soup = BeautifulSoup(faktura_response.text, "html.parser")
                        gross_row = soup.find("span", string="Gross(NOK)")
                        tr_counts = gross_row.find_all_next("tr")
                    
                        faktura = {} # Lista di dizionari per ogni articolo
                        for tr in tr_counts[:-4]:
                            td=tr.find_all("td")
                            faktura_id=td[1].text.strip()
                            total=td[5].text.strip()
                            faktura[faktura_id] = total


                        return app, kort, faktura

            return app, kort
        
        except Exception as e:
            pass


    def get_cards_payments(self,location):
        try:
            turnover_response=self.session.get(location)
            soup = BeautifulSoup(turnover_response.text, "html.parser")

            # Find the 'Total' value (final total at the bottom)
            total_element = soup.find("span", string="Turnover")
            if not total_element:
                return "", "", "", ""

            # Extract 'VISA' total
            visa_row = soup.find("span", string="Visa")
            visa_total=visa_row.find_next("span").find_next("span").find_next("span").text if visa_row else ""

            # Extract 'Mastercard' total
            mastercard_row = soup.find("span", string="Mastercard")
            mastercard_total=mastercard_row.find_next("span").find_next("span").find_next("span").text if mastercard_row else ""

            # Extract 'Bank Axept' total
            bank_axept_row = soup.find("span", string="Bank Axept")
            bank_axept_total=bank_axept_row.find_next("span").find_next("span").find_next("span").text if bank_axept_row else ""

            # Extract 'Maestro' total
            maestro_row = soup.find("span", string="Maestro")
            maestro_total=maestro_row.find_next("span").find_next("span").find_next("span").text if maestro_row else ""

            visa={'visa_total':visa_total}
            mastercard={'mastercard_total':mastercard_total}
            bank_axept={'bank_axept_total':bank_axept_total}
            maestro={'maestro_total':maestro_total}

            return visa, mastercard, bank_axept, maestro
        except Exception as e:
            pass

    def get_groups(self,location):
        try:
            turnover_response = requests.get(location)
            content = turnover_response.content  # Ottieni i byte grezzi
            content = content.decode('utf-8', errors='ignore')  # Decodifica manualmente con UTF-8
            soup = BeautifulSoup(content, "html.parser")

            gross_row = soup.find("span", string="Gross")
            tr_counts = gross_row.find_all_next("tr")
            
            groups = {} # Lista di dizionari per ogni articolo
            for tr in tr_counts[:-2]:
                td=tr.find_all("td")
                group=td[1].text.strip()
                total=td[6].text.strip()
                groups[group] = total
            return groups
        except Exception as e:
            pass


    def process_location(self,location_data):
        """Process a single location and return the result."""
        day = location_data['day']
        month = location_data['month']
        year = location_data['year']
        turnover_location = location_data['turnover']
        cards_location = location_data['cards']
        groups_location = location_data['groups']

        try:
            result = self.get_turnover(turnover_location, month, day, year)
                # Unpack the result with a default value for 'faktura'
            if len(result) == 3:
                app, kort, faktura = result
            else:
                app, kort = result
                faktura = None  # Default value if 'faktura' is not returned
            if not app and not kort:
                return
            visa, mastercard, bank_axept, maestro = self.get_cards_payments(cards_location)
            groups = self.get_groups(groups_location)
            return {
                'day': day,
                'app': app,
                'kort': kort,
                'cards': {
                    'visa': visa,
                    'mastercard': mastercard,
                    'bank_axept': bank_axept,
                    'maestro': maestro
                },
                'faktura': faktura,
                'groups': groups
            }
        except Exception as e:
            print(f"Error processing location for day {day}: {e}")
            return None
        
    def process_data(self, data, wb):
        # Process the data and write it to the sheet
        # change selected sheet
        if data['day'][0] == '0':
            day=data['day'][1:]
        else:
            day=data['day']
        
        sheet=wb[day]


        def parse_number(value):
            try:
                # Remove commas and convert to float
                return float(value.replace(',', ''))
            except (ValueError, AttributeError):
                return value  # Return the original value if conversion fails

        sheet['D13'] = parse_number(data['groups'].get('Brennevin', ''))
        sheet['D14'] = parse_number(data['groups'].get('Mat', ''))
        sheet['D15'] = parse_number(data['groups'].get('Mineralvann', ''))
        sheet['D16'] = parse_number(data['groups'].get('Vin', ''))
        sheet['D17'] = parse_number(data['groups'].get('Øl', ''))
        sheet['D18'] = parse_number(data['groups'].get('Cider/Rusbrus', ''))
        
        if data['cards']['bank_axept']:
            sheet['D38'] = parse_number(data['cards']['bank_axept'].get('bank_axept_total', ''))
        if data['cards']['visa']:
            sheet['D39'] = parse_number(data['cards']['visa'].get('visa_total', ''))
        if data['cards']['mastercard']:
            sheet['D40'] = parse_number(data['cards']['mastercard'].get('mastercard_total', ''))
        if data['cards']['maestro']:
            sheet['D41'] = parse_number(data['cards']['maestro'].get('maestro_total', ''))
        
        sheet['D43'] = parse_number(data['app'].get('app_order_total', ''))
        sheet['D107'] = parse_number(data['app'].get('app_order_tip', ''))
        sheet['D108'] = parse_number(data['kort'].get('kort_tip', ''))

        if data['faktura']:
            for row, (number, total) in enumerate(data['faktura'].items(), start=70):
                sheet[f'D{row}'] = number
                sheet[f'H{row}'] = parse_number(total)

        return


    def process_locations_multithreaded(self,locations):
        """Process all locations using multithreading."""
        results = []
        process_active[self.username] = "Extracting data from the reports..."
        with ThreadPoolExecutor(max_workers=5) as executor:  # Adjust max_workers as needed
            future_to_location = {executor.submit(self.process_location, loc): loc for loc in locations}

            for future in as_completed(future_to_location):
                result = future.result()
                if result:
                    results.append(result)
        return results

    def make_document(self,articles):
        global users_sessions
        # create a copy of the dummy document using windows or linux
        uuid = os.urandom(16).hex()
        os.system(f"cp {self.dummy_document} {uuid}.xlsx")
        self.dummy_document = f"{uuid}.xlsx"
        wb = load_workbook(self.dummy_document)
        process_active[self.username] = "Writing data to the document..."
        sheet=wb['Innstillinger']
        sheet['B11'] = users_sessions[self.username]['current_restaurant']
        sheet['B7'] = int(self.year)
        sheet['B5'] = int(self.month)
        sheet['B6'] = datetime.date(int(self.year), int(self.month), 1)
        self.month =  datetime.date(int(self.year), int(self.month), 1).strftime('%B')
        document = f"{users_sessions[self.username]['current_restaurant']} - Kassadagbok - {self.year} - {self.month} .xlsx"
        filename = os.path.basename(document)
        sheet=wb['Total']
        sheet['Q2'] = self.month
        for article in articles:
            self.process_data(article, wb)
        if not os.path.exists(f"files/{users_sessions[self.username]['current_restaurant']}"):
            os.makedirs(f"files/{users_sessions[self.username]['current_restaurant']}")
        wb.save(f"files/{users_sessions[self.username]['current_restaurant']}/{filename}")
        os.system(f"rm {self.dummy_document}")
        process_active[self.username] = "DONE"
        print(f"Document saved as {filename}")



#########################################################################################################################
###     FUNZIONI FLASK     ##############################################################################################
#########################################################################################################################
def cleanup_threads():
    """Join completed threads to free resources."""
    for username, user_process in list(users_sessions.items()):
        thread = user_process.get('process_thread')
        if thread and not thread.is_alive():  # Check if the thread has finished
            thread.join()  # Join the thread to clean up
            user_process['process_thread'] = None  # Remove the thread reference

def periodic_cleanup():
    """Periodically clean up completed threads."""
    cleanup_threads()
    Timer(60, periodic_cleanup).start()  # Run every 60 seconds

def sanitize_filename(filename):
    # Remove any path separators and keep only alphanumeric characters, dashes, underscores, and periods
    filename = re.sub(r'[^\w\-.]', '_', filename)
    return filename

def decodeJWT(token):
    try:
        decoded = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        username = decoded['username']
        return username
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token has expired'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 401



#########################################################################################################################
###     ROUTES FLASK     ###############################################################################################
#########################################################################################################################
@app.route('/')
def home():
    if request.cookies.get('token'):
        decodeJWT(request.cookies.get('token'))
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET'])
def login_page():
    # get the token from the cookie
    token = request.cookies.get('token')
    if token:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    if not data:
        return render_template('login.html')
    username = data.get('username')
    password = data.get('password')
    available_restaurants = {}

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400
    
    # Effettua il login a Gastrofix e salva la sessione per l'utente
    session = requests.Session()
    url = 'https://no.gastrofix.com/icash/login'
    data= {
        'joperator': '',
        'username': username,
        'password': password,
        'remember-me':'true'
    }

    response=session.post(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    if not response.json()['status'] == "FAIL":
        session.headers.update({'X-Xsrf-Token': session.cookies['XSRF-TOKEN']})
        response=session.get('https://no.gastrofix.com/icash/util/security.json')
        current_restaurant=response.json()['currentUser']['currentRestaurant']['name'].replace(' - Posthallen Drinkhub', '')
        for restaurant in response.json()['currentUser']['accessibleRestaurants']:
            # salva i restaurant as dictionary key as id and value as name
            available_restaurants[restaurant['id']] = restaurant['name'].replace(' - Posthallen Drinkhub', '')
        available_restaurants.pop(40836)
        available_restaurants.pop(40846)
        session.headers.update({'X-Xsrf-Token': session.cookies['XSRF-TOKEN']})

    # Crea un JWT per l'utente
        expiration_time = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        token = jwt.encode({'username': username, 'exp': expiration_time}, SECRET_KEY, algorithm='HS256')
        response = make_response(jsonify({'message': 'Login successful'}))
        response.set_cookie('token', token, httponly=True)
        users_sessions[username] = {'session': session, 'logged_in': True, 'current_restaurant': current_restaurant, 'available_restaurants': available_restaurants}
        return response
    return jsonify({'error': 'Invalid credentials'})

@app.route('/get_restaurants', methods=['GET'])
def get_restaurants():
    token = request.cookies.get('token')
    if not token:
        return jsonify({'error': 'Token is required'}), 400

    username = decodeJWT(token)
    if username not in users_sessions or not users_sessions[username]['logged_in']:
        return jsonify({'error': 'User not logged in'}), 401

    return jsonify(users_sessions[username]['available_restaurants'])

@app.route('/change_restaurant', methods=['POST'])
def change_restaurant():
    token = request.cookies.get('token')
    if not token:
        return jsonify({'error': 'Token is required'}), 400

    username = decodeJWT(token)
    if username not in users_sessions or not users_sessions[username]['logged_in']:
        return jsonify({'error': 'User not logged in'}), 401

    data = request.json
    restaurant_id = data.get('restaurant_id')
    if not restaurant_id:
        return jsonify({'error': 'Restaurant ID is required'}), 400

    session = users_sessions[username]['session']
    data= {"restaurant":{"id":restaurant_id}}

    response=session.post(f'https://no.gastrofix.com/icash/user/new/switchRestaurant.json', data=json.dumps(data), headers={'Content-Type': 'application/json;charset=utf-8'})
    response=session.get('https://no.gastrofix.com/icash/util/security.json')
    current_restaurant=response.json()['currentUser']['currentRestaurant']['name'].replace(' - Posthallen Drinkhub', '')
    users_sessions[username]['current_restaurant'] = current_restaurant
    return render_template('dashboard.html', username=current_restaurant)

@app.route('/dashboard')
def dashboard():
    token = request.cookies.get('token')
    if not token:
        return redirect(url_for('login'))
    username=decodeJWT(token)
    res=render_template('dashboard.html', username=users_sessions[username]['current_restaurant'])
    return res

@app.route('/files', methods=['GET'])
def get_files():
    """Restituisce la lista dei file nella sottocartella dell'utente dentro la cartella 'files'."""
    token = request.cookies.get('token')
    if not token:
        return jsonify({'error': 'Token is required'}), 400

    try:
        decoded = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        username = decoded['username']
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token has expired'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 401

    if username not in users_sessions or not users_sessions[username]['logged_in']:
        return jsonify({'error': 'User not logged in'}), 401

    user_folder = os.path.join(files_directory, users_sessions[username]['current_restaurant'])

    if not os.path.exists(user_folder):
        return jsonify({'files': []})

    user_files = [f for f in os.listdir(user_folder) if f.endswith(".xlsx")]
    return jsonify({'files': user_files})

@app.route('/download/<filename>', methods=['GET'])
def download_file(filename):
    """Restituisce il file richiesto dall'utente."""
    token = request.cookies.get('token')
    if not token:
        return jsonify({'error': 'Token is required'}), 400

    username = decodeJWT(token)

    if username not in users_sessions or not users_sessions[username]['logged_in']:
        return jsonify({'error': 'User not logged in'}), 401

    user_folder = os.path.join(files_directory, users_sessions[username]['current_restaurant'])
    # Sanitize the filename to prevent path traversal attacks

    filename = os.path.basename(filename)

    # Optionally, validate the filename extension
    if not filename.endswith('.xlsx'):
        return jsonify({'error': 'Invalid file type'}), 400


    file_path = os.path.join(user_folder, filename)

    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Ensure the file path is within the user's folder
    if not os.path.abspath(file_path).startswith(os.path.abspath(user_folder)):
        return jsonify({'error': 'Invalid file path'}), 400


    return send_file(file_path, as_attachment=True)

@app.route('/status', methods=['GET'])
def get_status():
    """Restituisce lo stato del processo."""
    token = request.cookies.get('token')
    if not token:
        return jsonify({'error': 'Token is required'}), 400

    username = decodeJWT(token)

    if username not in process_active:
        return jsonify({'status': 'Not processing a report at the moment'})

    status = process_active[username]
    return jsonify({'status': status})

@app.route('/processdata', methods=['POST'])
def processdata():
    """Avvia il processo per l'utente."""
    token = request.cookies.get('token')
    if not token:
        return jsonify({'error': 'Token is required'}), 400

    username = decodeJWT(token)

    if username not in users_sessions or not users_sessions[username]['logged_in']:
        return jsonify({'error': 'User not logged in'}), 401

    if username in process_active and process_active[username]:
        return jsonify({'error': 'Process already running'}), 400

    # Crea un processo separato per l'utente
    session = users_sessions[username]['session']

    data = request.json
    month = data.get('month')
    year = data.get('year')
    user_process = UserProcess(username, session, month, year)

    # Create and start the thread
    process_thread = Thread(target=user_process.run_process)
    process_thread.start()

    # Store the thread in the user's session
    users_sessions[username]['process_thread'] = process_thread

    # Optionally join the thread here (blocking)
    # process_thread.join()

    return jsonify({'message': 'Process started successfully'}), 200


# Start the periodic cleanup when the app starts
if __name__ == '__main__':
    periodic_cleanup()  # Start the cleanup task
    app.run(host='0.0.0.0', port=5003)

