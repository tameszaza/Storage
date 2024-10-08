import logging
import os
import shutil
import json
from flask import Flask, request, redirect, url_for, send_from_directory, render_template, session, abort, send_file, jsonify, Response, flash
from flask_bcrypt import Bcrypt
import subprocess
import psutil
import time
import tempfile
from datetime import datetime, timedelta
import uuid
import google.generativeai as genai
import PIL.Image
import io
from collections import deque
import matplotlib.pyplot as plt
import uuid  # for generating unique filenames
import matplotlib.patheffects as path_effects

# Set up logging
logging.basicConfig(
    filename='server.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s',
)

DATA_TRANSFER_LOG = 'data_transfer.log'
USER_DATA_FILE = 'users.json'
FEEDBACK_FILE = 'feedback.json'

def load_users():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r') as file:
            users = json.load(file)
        return users
    return {}

def save_users(users):
    with open(USER_DATA_FILE, 'w') as file:
        json.dump(users, file)

app = Flask(__name__)
bcrypt = Bcrypt(app)

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 * 1024  # 5 GB limit
app.secret_key = 'supersecretkey'  # Change this to a random secret key
def load_api_key():
    with open('uploads/Admin/config.txt', 'r') as file:
        return file.read().strip()

genai.configure(api_key=load_api_key())
model = genai.GenerativeModel("gemini-1.5-flash")

# In-memory user storage. Replace with a proper database in production.
users = load_users()

def format_modification_time(mod_time):
    now = datetime.now()
    mod_datetime = datetime.fromtimestamp(mod_time)

    # If the file was modified today
    if mod_datetime.date() == now.date():
        return mod_datetime.strftime("%H:%M")

    # If the file was modified yesterday
    elif mod_datetime.date() == (now - timedelta(days=1)).date():
        return "Yesterday"

    # If the file was modified within the last 6 days (but not today or yesterday)
    elif now - timedelta(days=6) <= mod_datetime < now:
        return mod_datetime.strftime("%A, %d %B")

    # If the file was modified earlier this year
    elif mod_datetime.year == now.year:
        return mod_datetime.strftime("%d %B")

    # If the file was modified in a previous year
    else:
        return mod_datetime.strftime("%d %B %Y")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users:
            return "Username already exists!"
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        users[username] = {'password': hashed_password, 'suspended': False}
        save_users(users)  # Save users to file
        user_path = os.path.join(app.config['UPLOAD_FOLDER'], username)
        if not os.path.exists(user_path):
            os.makedirs(user_path)
        logging.info(f"New user registered: {username}")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and bcrypt.check_password_hash(users[username]['password'], password):
            if users[username].get('suspended'):
                return "Account is suspended!"
            session['logged_in'] = True
            session['username'] = username
            logging.info(f"User {username} logged in.")
            if username == 'Admin':
                return redirect(url_for('index'))
            else:
                return redirect(url_for('user_folder', username=username))
        else:
            logging.warning(f"Failed login attempt for username: {username}")
            return "Invalid username or password!"
    return render_template('login.html')

@app.route('/index', defaults={'path': ''})
@app.route('/index/<path:path>')
def index(path):
    if not session.get('logged_in'):
        logging.warning("Unauthorized access attempt to index.")
        return redirect(url_for('login'))
    
    # Ensure user stays within their directory
    if session['username'] != 'Admin':
        user_base_path = session['username']
        if not path.startswith(user_base_path):
            path = user_base_path

    current_path = os.path.join(app.config['UPLOAD_FOLDER'], path)
    if not os.path.exists(current_path):
        logging.error(f"Folder not found: {current_path}")
        return "Folder not found!", 404

    search_query = request.args.get('search')
    files = []

    if search_query:
        # Search mode: find files and folders that match the query
        for root, dirs, file_list in os.walk(current_path):
            for file_name in file_list:
                if search_query.lower() in file_name.lower():
                    file_path = os.path.join(root, file_name)
                    size = os.path.getsize(file_path)
                    mod_time = os.path.getmtime(file_path)

                    files.append({
                        'name': os.path.relpath(file_path, current_path),
                        'is_dir': False,
                        'size': size,
                        'mod_time': format_modification_time(mod_time)
                    })
            for dir_name in dirs:
                if search_query.lower() in dir_name.lower():
                    dir_path = os.path.join(root, dir_name)
                    files.append({
                        'name': os.path.relpath(dir_path, current_path) + '/',
                        'is_dir': True,
                        'size': None,
                        'mod_time': None
                    })
    else:
        # Normal mode: list files and folders in the current directory only
        for f in os.listdir(current_path):
            file_path = os.path.join(current_path, f)
            is_dir = os.path.isdir(file_path)
            size = os.path.getsize(file_path) if not is_dir else get_folder_size(file_path)
            mod_time = os.path.getmtime(file_path)

            # Read content for .txt, .py, and .log files
            content = None
            if not is_dir and f.endswith(('.txt', '.py', '.log')):
                with open(file_path, 'r') as file:
                    lines = file.readlines()[:6]  # Limit to first 5 lines
                    content = ''.join(lines)
                    if len(lines) == 6:
                        content += '...'  # Indicate that there is more content

            files.append({
                'name': f,
                'is_dir': is_dir,
                'size': size,
                'mod_time': format_modification_time(mod_time),
                'content': content
            })
    
    # Sort files: directories first, then by name
    files.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))

    return render_template('index.html', files=files, path=path, search_query=search_query)





def load_feedback():
    if os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE, 'r') as file:
            try:
                feedback_list = json.load(file)
                if not isinstance(feedback_list, list):
                    feedback_list = []  # Ensure feedback_list is a list
            except json.JSONDecodeError:
                feedback_list = []
    else:
        feedback_list = []
    return feedback_list

def mark_all_feedback_as_read():
    feedback_list = load_feedback()
    for feedback in feedback_list:
        feedback['read'] = True

    with open(FEEDBACK_FILE, 'w') as file:
        json.dump(feedback_list, file, indent=4)  # Added indent for better readability
    return feedback_list


def get_folder_size(folder_path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return total_size

@app.route('/user/<username>')
def user_folder(username):
    if not session.get('logged_in') or session.get('username') != username:
        logging.warning(f"Unauthorized access attempt to user folder: {username}")
        return redirect(url_for('login'))
    user_path = os.path.join(app.config['UPLOAD_FOLDER'], username)
    if not os.path.exists(user_path):
        os.makedirs(user_path)
    files = os.listdir(user_path)
    files = [{'name': f, 'is_dir': os.path.isdir(os.path.join(user_path, f))} for f in files]
    return render_template('index.html', files=files, path=username)

@app.route('/upload', methods=['POST'])
@app.route('/upload/<path:path>', methods=['POST'])
def upload_file(path=''):
    if not session.get('logged_in'):
        logging.warning("Unauthorized upload attempt.")
        return redirect(url_for('login'))
    if 'file' not in request.files:
        return redirect(request.url)
    files = request.files.getlist('file')
    if not files or files[0].filename == '':
        return redirect(request.url)
    current_path = os.path.join(app.config['UPLOAD_FOLDER'], path)
    if not os.path.exists(current_path):
        os.makedirs(current_path)
    for file in files:
        if file and file.filename != '':
            filepath = os.path.join(current_path, file.filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)  # Ensure directories exist
            file.save(filepath)
            logging.info(f"File uploaded: {filepath}")
    return redirect(url_for('index', path=path))


@app.route('/uploads/<path:path>/<filename>')
def uploaded_file(path, filename):
    if not session.get('logged_in'):
        logging.warning("Unauthorized file access attempt.")
        return redirect(url_for('login'))
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], path), filename)

@app.route('/download/<path:path>/<filename>')
def download_file(path, filename):
    if not session.get('logged_in'):
        logging.warning("Unauthorized download attempt.")
        return redirect(url_for('login'))
    logging.info(f"File downloaded: {os.path.join(app.config['UPLOAD_FOLDER'], path, filename)}")
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], path), filename, as_attachment=True)

@app.route('/download_folder/<path:path>', methods=['POST'])
def download_folder(path):
    if not session.get('logged_in'):
        logging.warning("Unauthorized folder download attempt.")
        return redirect(url_for('login'))

    folder_path = os.path.join(app.config['UPLOAD_FOLDER'], path)

    if not os.path.exists(folder_path):
        logging.error(f"Folder not found: {folder_path}")
        abort(404)

    # Create a zip file
    zip_filename = f"{os.path.basename(folder_path)}.zip"
    zip_path = os.path.join(app.config['UPLOAD_FOLDER'], zip_filename)
    shutil.make_archive(zip_path.replace('.zip', ''), 'zip', folder_path)

    return send_file(zip_path, as_attachment=True, mimetype='application/zip')

@app.route('/download_selected', methods=['POST'])
def download_selected():
    selected_files = request.form.getlist('selected_files')
    current_path = request.form.get('current_path', '')  # Assuming you pass the current path from the form
    if not selected_files:
        return jsonify(success=False)

    # Create a temporary directory to store the selected files
    with tempfile.TemporaryDirectory() as temp_dir:
        for file_name in selected_files:
            # Create a file path for the selected file in the uploads directory
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], current_path, file_name)
            if os.path.exists(file_path):
                # Copy the file to the temporary directory
                shutil.copy(file_path, os.path.join(temp_dir, file_name))
            else:
                logging.warning(f"File not found: {file_path}")

        # Create a zip file of the temporary directory
        zip_filename = 'selected_files.zip'
        zip_path = os.path.join(app.config['UPLOAD_FOLDER'], zip_filename)
        shutil.make_archive(zip_path.replace('.zip', ''), 'zip', temp_dir)

        # Send the zip file as a response
        return send_file(zip_path, as_attachment=True, mimetype='application/zip')

@app.route('/toggle_dark_mode', methods=['POST'])
def toggle_dark_mode():
    mode = request.json.get('mode')
    if mode in ['dark', 'light']:
        session['dark_mode'] = mode
    return jsonify(success=True)

@app.route('/delete_selected', methods=['POST'])
def delete_selected():
    selected_files = request.form.getlist('selected_files')
    if not selected_files:
        return jsonify(success=False)

    for file_name in selected_files:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_name)
        if os.path.exists(file_path):
            if os.path.isdir(file_path):
                shutil.rmtree(file_path)
            else:
                os.remove(file_path)

    return jsonify(success=True)
# Ensure that after sending the file, the zip is deleted

@app.after_request
def remove_zip_file(response):
    content_disposition = response.headers.get('Content-Disposition', '')
    if 'filename=' in content_disposition:
        zip_filename = content_disposition.split('filename=')[-1].strip('"')
        zip_full_path = os.path.join(app.config['UPLOAD_FOLDER'], zip_filename)
        if os.path.exists(zip_full_path):
            os.remove(zip_full_path)
    return response

@app.route('/delete/<path:path>/<filename>', methods=['POST'])
def delete_file(path, filename):
    if not session.get('logged_in'):
        logging.warning("Unauthorized delete attempt.")
        return redirect(url_for('login'))
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], path, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        logging.info(f"File deleted: {filepath}")
    return redirect(url_for('index', path=path))

@app.route('/delete_folder', methods=['POST'])
@app.route('/delete_folder/<path:path>', methods=['POST'])
def delete_folder(path=''):
    if not session.get('logged_in'):
        logging.warning("Unauthorized folder delete attempt.")
        return redirect(url_for('login'))
    folder_path = os.path.join(app.config['UPLOAD_FOLDER'], path)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        logging.info(f"Folder deleted: {folder_path}")
    parent_path = '/'.join(path.split('/')[:-1])
    return redirect(url_for('index', path=parent_path))

@app.route('/edit_file/<path:path>/<filename>')
def edit_file(path, filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], path, filename)
    try:
        with open(file_path, 'r') as file:
            content = file.read()
    except IsADirectoryError:
        flash('Error: The specified path is a directory, not a file.', 'danger')
        return redirect(url_for('index', path=path))

    return render_template('editfile.html', filename=filename, content=content, path=path)




@app.route('/save_file/<path:path>/<filename>', methods=['POST'])
def save_file(path, filename):
    file_content = request.form['file_content']
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], path, filename)
    
    # Save the content to the file
    with open(file_path, 'w') as file:
        file.write(file_content)
    
    flash('File saved successfully!', 'success')
    return redirect(url_for('index', path=path))



@app.route('/rename_file/<path:path>/<filename>', methods=['POST'])
def rename_file(path, filename):
    if not session.get('logged_in'):
        logging.warning("Unauthorized rename attempt.")
        return redirect(url_for('login'))

    # Check if the user is authorized to rename this file
    if session['username'] != 'Admin' and not filename.startswith(session['username']):
        logging.warning(f"Unauthorized rename attempt by user {session['username']} for file {filename}.")
        return redirect(url_for('index', path=path))

    new_filename = request.form['new_name'].strip()
    if not new_filename:
        logging.warning("Empty new filename provided.")
        return redirect(url_for('index', path=path))

    current_filepath = os.path.join(app.config['UPLOAD_FOLDER'], path, filename)
    new_filepath = os.path.join(app.config['UPLOAD_FOLDER'], path, new_filename)

    if not os.path.exists(current_filepath):
        logging.error(f"File not found: {current_filepath}")
        abort(404)

    if os.path.exists(new_filepath):
        logging.warning("New filename already exists.")
        return redirect(url_for('index', path=path))

    os.rename(current_filepath, new_filepath)
    logging.info(f"File renamed from {current_filepath} to {new_filepath}")

    return redirect(url_for('index', path=path))

@app.route('/rename_folder/<path:path>/<foldername>', methods=['POST'])
def rename_folder(path, foldername):
    if not session.get('logged_in'):
        logging.warning("Unauthorized rename attempt.")
        return redirect(url_for('login'))

    new_folder_name = request.form['new_name'].strip()
    if not new_folder_name:
        logging.warning("Empty new folder name provided.")
        return redirect(url_for('index', path=os.path.dirname(path)))

    current_folder_path = os.path.join(app.config['UPLOAD_FOLDER'], path, foldername)
    new_folder_path = os.path.join(app.config['UPLOAD_FOLDER'], path, new_folder_name)

    if not os.path.exists(current_folder_path):
        logging.error(f"Folder not found: {current_folder_path}")
        abort(404)

    if os.path.exists(new_folder_path):
        logging.warning("New folder name already exists.")
        return redirect(url_for('index', path=os.path.dirname(path)))

    os.rename(current_folder_path, new_folder_path)
    logging.info(f"Folder renamed from {current_folder_path} to {new_folder_path}")

    return redirect(url_for('index', path=path))

@app.route('/create_folder', methods=['POST'])
@app.route('/create_folder/<path:path>', methods=['POST'])
def create_folder(path=''):
    if not session.get('logged_in'):
        logging.warning("Unauthorized folder creation attempt.")
        return redirect(url_for('login'))
    folder_name = request.form['folder_name'].strip()
    
    # Check for illegal folder names
    if folder_name == '' or folder_name[-1] == ' ' or folder_name.startswith('.') or '/' in folder_name or '\\' in folder_name:
        logging.warning(f"Invalid folder name attempt: {folder_name}")
        return "Invalid folder name!", 400

    current_path = os.path.join(app.config['UPLOAD_FOLDER'], path)
    new_folder_path = os.path.join(current_path, folder_name)
    if not os.path.exists(new_folder_path):
        os.makedirs(new_folder_path)
        logging.info(f"Folder created: {new_folder_path}")
    return redirect(url_for('index', path=path))

@app.route('/admin/logs')
def admin_logs():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    
    log_file_path = 'server.log'
    try:
        with open(log_file_path, 'r') as file:
            log_contents = file.read()
    except FileNotFoundError:
        log_contents = "Log file not found."
    
    return render_template('admin_logs.html', log_contents=log_contents)

@app.route('/admin/clear_transfer_logs', methods=['POST'])
def clear_transfer_logs():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    
    try:
        with open(DATA_TRANSFER_LOG, 'w') as file:
            file.write('')  # Clear the log file
        logging.info('Log file cleared by Admin.')
    except Exception as e:
        logging.error(f'Error clearing log file: {str(e)}')
        return f'Error clearing log file: {str(e)}', 500

    return redirect(url_for('view_transfer_logs'))

@app.route('/admin/transfer_logs')
def view_transfer_logs():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    
    try:
        with open(DATA_TRANSFER_LOG, 'r') as file:
            log_contents = file.read()
    except FileNotFoundError:
        log_contents = "Transfer log file not found."
    
    return render_template('transfer_logs.html', log_contents=log_contents)

@app.route('/admin/clear_logs', methods=['POST'])
def clear_logs():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    
    log_file_path = 'server.log'
    try:
        with open(log_file_path, 'w') as file:
            file.write('')  # Clear the log file
        logging.info('Log file cleared by Admin.')
    except Exception as e:
        logging.error(f'Error clearing log file: {str(e)}')
        return f'Error clearing log file: {str(e)}', 500

    return redirect(url_for('admin_logs'))

@app.route('/admin/delete_feedback/<feedback_id>', methods=['POST'])
def delete_feedback(feedback_id):
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))

    if os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE, 'r') as file:
            feedback_list = json.load(file)

        feedback_list = [f for f in feedback_list if f['id'] != feedback_id]

        with open(FEEDBACK_FILE, 'w') as file:
            json.dump(feedback_list, file)

    return redirect(url_for('view_feedback'))


@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    feedback_submitted = False
    if request.method == 'POST':
        feedback_data = {
            'id': str(uuid.uuid4()),  # Generate a unique ID for each feedback
            'username': session.get('username', 'Anonymous'),
            'feedback_type': request.form['feedback_type'],
            'message': request.form['message'],
            'rating': request.form.get('rating'),
            'timestamp': datetime.now().isoformat(),  # Add timestamp
            'read': False  # Mark as unread initially
        }

        # Load existing feedback from the JSON file
        if os.path.exists(FEEDBACK_FILE):
            with open(FEEDBACK_FILE, 'r') as file:
                try:
                    feedback_list = json.load(file)
                    if not isinstance(feedback_list, list):
                        feedback_list = []  # Ensure feedback_list is a list
                except json.JSONDecodeError:
                    feedback_list = []
        else:
            feedback_list = []

        # Append the new feedback data
        feedback_list.append(feedback_data)

        # Write the updated feedback list back to the JSON file
        with open(FEEDBACK_FILE, 'w') as file:
            json.dump(feedback_list, file)

        feedback_submitted = True

    return render_template('feedback.html', feedback_submitted=feedback_submitted)




@app.route('/chat', methods=['GET'])
def chat_page():
    # Clear the conversation history when the chat page is accessed
    session.pop('conversation_history', None)
    return render_template('chat.html')



@app.route('/chat', methods=['POST'])
def chat():
    if 'conversation_history' not in session:
        session['conversation_history'] = deque(maxlen=1000)
        
        # Initialize the conversation with the user's name and role
        initial_message = (
            f"My name is {session.get('username')}. "
            "I am the user of this file management system, and I need assistance with managing my files."
        )
        session['conversation_history'].append({"role": "user", "parts": initial_message})
        
        # Include file structure information
        file_structure = get_user_file_structure(session.get('username'))  # Function to get file structure
        file_structure_message = f"The current file structure is as follows:\n{file_structure}"
        session['conversation_history'].append({"role": "user", "parts": file_structure_message})
        
        # Start the conversation with the AI model
        chat = model.start_chat(history=list(session['conversation_history']))
        response = chat.send_message(initial_message)
        session['conversation_history'].append({"role": "model", "parts": response.text})

    conversation_history = session['conversation_history']
    msg = request.form.get("msg")
    image = request.files.get("image")
    prompt = request.form.get("prompt")

    response_text = ""

    if image:
        # Handle image input
        image = PIL.Image.open(io.BytesIO(image.read()))
        prompt = prompt or "Describe this image."
        response = model.generate_content([prompt, image])
        conversation_history.append({"role": "user", "parts": "Image sent: " + (msg or '')})
        conversation_history.append({"role": "model", "parts": response.text})
        response_text = response.text
    elif msg:
        # Handle text input
        conversation_history.append({"role": "user", "parts": msg})
        chat = model.start_chat(history=list(conversation_history))
        response = chat.send_message(msg)
        conversation_history.append({"role": "model", "parts": response.text})
        response_text = response.text
    else:
        return jsonify({"response": "No valid input provided"}), 400

    session['conversation_history'] = list(conversation_history)  # Convert deque to list before storing
    return jsonify({"response": response_text})

def get_user_file_structure(username):
    user_folder_path = os.path.join(app.config['UPLOAD_FOLDER'], username)
    structure = []

    for root, dirs, files in os.walk(user_folder_path):
        level = root.replace(user_folder_path, '').count(os.sep)
        indent = ' ' * 4 * level
        structure.append(f"{indent}{os.path.basename(root)}/")
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            structure.append(f"{subindent}{f}")
    
    return '\n'.join(structure)

@app.route('/detail/<path:directory>', methods=['GET'])
def detail(directory):
    try:
        # Handle root directory access
        if directory == 'Root':
            if session['username'] != 'Admin':
                return "Unauthorized access", 403
            directory = ''  # Root of the uploads directory

        # Ensure the user stays within their directory
        if session['username'] != 'Admin':
            user_base_path = os.path.join(app.config['UPLOAD_FOLDER'], session['username'])
            abs_directory_path = os.path.join(app.config['UPLOAD_FOLDER'], directory)

            # Normalize paths for comparison
            user_base_path = os.path.normpath(user_base_path)
            abs_directory_path = os.path.normpath(abs_directory_path)

            if not abs_directory_path.startswith(user_base_path):
                return "Unauthorized access", 403
        else:
            # For Admin, calculate the absolute path without restriction
            abs_directory_path = os.path.join(app.config['UPLOAD_FOLDER'], directory)

        # Check if the directory exists
        if not os.path.exists(abs_directory_path) or not os.path.isdir(abs_directory_path):
            return "Directory not found!", 404

        # Calculate space usage for the directory and file types
        directory_data, file_type_data = analyze_directory_space(abs_directory_path)

        # Count total files and calculate total storage size
        file_count = sum(len(files) for _, _, files in os.walk(abs_directory_path))
        total_storage = sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(abs_directory_path) for f in files) / (1024 ** 2)  # MB

        # Generate a unique filename for the pie charts
        chart_path, chart_info = generate_pie_chart(directory_data)
        file_type_chart_path, file_type_chart_info = generate_pie_chart(file_type_data, is_file_type=True)

        # Pass only the filenames to the template
        chart_filename = os.path.basename(chart_path)
        file_type_chart_filename = os.path.basename(file_type_chart_path)

        return render_template('detail.html', 
                               directory=directory, 
                               chart_filename=chart_filename, 
                               file_type_chart_filename=file_type_chart_filename, 
                               file_count=file_count, 
                               total_storage=round(total_storage, 2),
                               chart_info=chart_info,
                               file_type_chart_info=file_type_chart_info)  # Pass both pie chart info to the template
    except Exception as e:
        app.logger.error(f"Error generating chart: {e}")
        return str(e), 500



@app.route('/charts/<filename>')
def serve_chart(filename):
    print(os.path.join(app.root_path, 'uploads', 'Admin', 'charts'), filename)
    return send_from_directory(os.path.join(app.root_path, 'uploads', 'Admin', 'charts'), filename)




def analyze_directory_space(directory):
    directory_data = {}
    file_type_data = {}
    
    for root, dirs, files in os.walk(directory):
        total_size = 0
        for file in files:
            file_path = os.path.join(root, file)
            size = os.path.getsize(file_path)
            total_size += size

            # Collect file type data
            ext = os.path.splitext(file)[1].lower()  # Get the file extension and normalize it
            if ext:
                if ext not in file_type_data:
                    file_type_data[ext] = 0
                file_type_data[ext] += size
        
        directory_data[root] = total_size
    
    return directory_data, file_type_data  # Return both directory and file type data




def generate_pie_chart(data, is_file_type=False):
    labels = []
    sizes = []
    total_size = sum(data.values())
    
    # Define a threshold to merge small sections (e.g., less than 1% of total)
    threshold = total_size * 0.01
    other_size = 0
    
    # Process the data to merge small sections
    chart_info = []  # This will store (label, size, percentage) tuples
    for key, size in data.items():
        if size < threshold:
            other_size += size
        else:
            if is_file_type:
                label = f"{key} ({size // (1024 ** 2)} MB)"  # Use file extension as the label
            else:
                label = f"{os.path.basename(key)} ({size // (1024 ** 2)} MB)"  # Use directory name as the label
            percentage = (size / total_size) * 100
            labels.append(label)
            sizes.append(size)
            chart_info.append((label, size // (1024 ** 2), round(percentage, 1)))  # Append as MB and percentage
    
    # Add 'Other' section if needed
    if other_size > 0:
        other_label = f"Other ({other_size // (1024 ** 2)} MB)"
        labels.append(other_label)
        sizes.append(other_size)
        percentage = (other_size / total_size) * 100
        chart_info.append((other_label, other_size // (1024 ** 2), round(percentage, 1)))
    
    fig, ax = plt.subplots(figsize=(10, 10))  # Increase the figure size
    
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, startangle=90, colors=plt.cm.Paired.colors,
        wedgeprops={'edgecolor': 'black'}, autopct='%1.1f%%', textprops={'fontsize': 10, 'color': 'black'}
    )
    
    # Function to add a glowing background behind text
    def add_glow(text):
        text.set_path_effects([
            path_effects.withStroke(linewidth=3, foreground="white", alpha=0.8),
            path_effects.Normal()
        ])
    
    # Adjust the font size and add the glow effect
    for text in texts + autotexts:
        text.set_fontsize(10)
        text.set_color('black')
        add_glow(text)
    
    ax.axis('equal')
    
    # Save with a transparent background
    charts_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'Admin', 'charts')
    os.makedirs(charts_dir, exist_ok=True)
    
    chart_filename = f"{uuid.uuid4()}.png"
    chart_path = os.path.join(charts_dir, chart_filename)
    
    plt.savefig(chart_path, transparent=True,  dpi=300)
    plt.close()
    
    return chart_path, chart_info  # Return both the chart path and the info



@app.route('/admin/clear_charts', methods=['POST'])
def clear_charts():
    charts_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'Admin', 'charts')

    try:
        # Delete all files in the charts directory
        for filename in os.listdir(charts_dir):
            file_path = os.path.join(charts_dir, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)

        flash('All charts have been successfully cleared.', 'success')
    except Exception as e:
        app.logger.error(f"Error clearing charts: {e}")
        flash(f"Error clearing charts: {e}", 'danger')

    return redirect(url_for('admin'))

@app.route('/admin/view_feedback')
def view_feedback():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))

    feedback_list = mark_all_feedback_as_read()
    return render_template('view_feedback.html', feedback_list=feedback_list)

@app.after_request
def log_response(response):
    # Log the data transfer size
    if not response.direct_passthrough:
        data_size = len(response.get_data())
        with open(DATA_TRANSFER_LOG, 'a') as f:
            f.write(f"{datetime.now()}: {request.remote_addr} - {data_size} bytes transferred.\n")
        logging.debug(f"Data size: {data_size} bytes transferred.")
    return response

@app.before_request
def log_request():
    with open(DATA_TRANSFER_LOG, 'a') as f:
        data_size = request.content_length or 0
        log_message = f"Incoming Request - Path: {request.path}, Method: {request.method}, Data Size: {data_size} bytes\n"
        f.write(log_message)

@app.route('/admin/reset/<username>', methods=['POST'])
def reset_user(username):
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    if username in users:
        del users[username]
        save_users(users)  # Save changes to file
        logging.info(f"User {username} reseted by Admin.")
    return redirect(url_for('admin'))

@app.route('/admin/remove/<username>', methods=['POST'])
def remove_user(username):
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    if username in users:
        del users[username]
        save_users(users)  # Save changes to file
        user_path = os.path.join(app.config['UPLOAD_FOLDER'], username)
        if os.path.exists(user_path):
            shutil.rmtree(user_path)
        logging.info(f"User {username} removed by Admin.")
    return redirect(url_for('admin'))

@app.route('/admin/suspend/<username>', methods=['POST'])
def suspend_user(username):
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    if username in users:
        users[username]['suspended'] = True
        save_users(users)  # Save changes to file
        logging.info(f"User {username} suspended by Admin.")
    return redirect(url_for('admin'))

@app.route('/admin/unsuspend/<username>', methods=['POST'])
def unsuspend_user(username):
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    if username in users and 'suspended' in users[username]:
        users[username]['suspended'] = False
        save_users(users)  # Save changes to file
        logging.info(f"User {username} unsuspended by Admin.")
    return redirect(url_for('admin'))

def get_user_count():
    # Assume users are stored in a database or directory
    users_dir = os.path.join(app.config['UPLOAD_FOLDER'])
    return len([name for name in os.listdir(users_dir) if os.path.isdir(os.path.join(users_dir, name))])

def get_total_storage():
    total_size = 0
    uploads_folder = app.config['UPLOAD_FOLDER']
    
    for dirpath, dirnames, filenames in os.walk(uploads_folder):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    
    # Convert to MB or GB based on size
    if total_size > 1024 ** 3:  # GB
        return f"{total_size / (1024 ** 3):.2f} GB"
    elif total_size > 1024 ** 2:  # MB
        return f"{total_size / (1024 ** 2):.2f} MB"
    else:  # KB
        return f"{total_size / 1024:.2f} KB"

@app.route('/introduction')
def introduction():
    user_count = get_user_count()
    total_storage = get_total_storage()
    return render_template('introduction.html', user_count=user_count, total_storage=total_storage)



@app.route('/admin/git_pull', methods=['POST'])
def git_pull():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    try:
        # Remove the problematic reference
        subprocess.run(['git', 'update-ref', '-d', 'refs/remotes/origin/main'], check=True)

        # Perform the git pull operation
        result = subprocess.run(['git', 'pull'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        logging.info(f"Git pull stdout: {result.stdout}")
        logging.info(f"Git pull stderr: {result.stderr}")

        if result.returncode == 0:
            return f"Git pull completed successfully:<br>{result.stdout.replace('\n', '<br>')}", 200
        else:
            logging.error(f"Git pull failed: {result.stderr}")
            return f"Git pull failed with error:<br>{result.stderr.replace('\n', '<br>')}", 500
    except subprocess.CalledProcessError as e:
        logging.error(f"Git pull exception: {str(e)}")
        return f"Git pull exception: {str(e)}", 500

@app.route('/system_usage')
def system_usage():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))

    # Gather system usage data
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')

    data = {
        'cpu_usage': cpu_usage,
        'memory_usage': memory_info.percent,
        'memory_used': (memory_info.total - memory_info.available) // (1024 ** 2),
        'memory_total': memory_info.total // (1024 ** 2),
        'disk_usage': disk_info.percent,
        'disk_used': disk_info.used // (1024 ** 3),
        'disk_total': disk_info.total // (1024 ** 3)
    }
    
    return jsonify(data)



@app.route('/admin')
def admin():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))

    # Gather system usage data
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    uptime_seconds = time.time() - psutil.boot_time()
    uptime_string = str(timedelta(seconds=int(uptime_seconds)))

    # Gather user storage usage
    user_storage = {}
    for user in users:
        user_folder = os.path.join(app.config['UPLOAD_FOLDER'], user)
        if os.path.exists(user_folder):
            total_size = 0
            for dirpath, dirnames, filenames in os.walk(user_folder):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    total_size += os.path.getsize(fp)
            user_storage[user] = total_size
    feedback_list = load_feedback()
    unread_count = sum(1 for feedback in feedback_list if not feedback['read'])

    return render_template('admin_panel.html', users=users, cpu_usage=cpu_usage, memory_info=memory_info, disk_info=disk_info, user_storage=user_storage, uptime=uptime_string, unread_count=unread_count)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# Other routes ...

def shutdown_server():
    os._exit(0)

@app.route('/admin/shutdown', methods=['POST'])
def shutdown():
    if session.get('username') != 'Admin':
        logging.warning("Unauthorized shutdown attempt")
        return redirect(url_for('login'))
    logging.info("Shutdown initiated by admin")
    shutdown_server()
    return 'Server shutting down...'

@app.route('/logout')
def logout():
    logging.info(f"User {session.get('username')} logged out.")
    session['logged_in'] = False
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True,host='0.0.0.0', port=5000)
