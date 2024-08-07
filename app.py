import logging
import os
import shutil
import json
from flask import Flask, request, redirect, url_for, send_from_directory, render_template, session, abort, send_file, jsonify, Response
from flask_bcrypt import Bcrypt
import subprocess
import mimetypes
import psutil
import time
import datetime
import cv2

# Set up logging
logging.basicConfig(
    filename='server.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s',
)

USER_DATA_FILE = 'users.json'

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

# In-memory user storage. Replace with a proper database in production.
users = load_users()

data_transfer_log = 'data_transfer.log'

@app.after_request
def log_response(response):
    with open(data_transfer_log, 'a') as f:
        data_size = len(response.get_data())
        log_message = f"Outgoing Response - Path: {request.path}, Method: {request.method}, Status: {response.status_code}, Data Size: {data_size} bytes\n"
        f.write(log_message)
    return response

@app.before_request
def log_request():
    with open(data_transfer_log, 'a') as f:
        data_size = request.content_length or 0
        log_message = f"Incoming Request - Path: {request.path}, Method: {request.method}, Data Size: {data_size} bytes\n"
        f.write(log_message)

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
    files = os.listdir(current_path)
    files = [{'name': f, 'is_dir': os.path.isdir(os.path.join(current_path, f)), 'size': os.path.getsize(os.path.join(current_path, f)) if not os.path.isdir(os.path.join(current_path, f)) else get_folder_size(os.path.join(current_path, f))} for f in files]
    return render_template('index.html', files=files, path=path)

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
    uptime_string = str(datetime.timedelta(seconds=int(uptime_seconds)))

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

    return render_template('admin_panel.html', users=users, cpu_usage=cpu_usage, memory_info=memory_info, disk_info=disk_info, user_storage=user_storage, uptime=uptime_string)

def gen_frames():
    camera = cv2.VideoCapture(0)  # Use 0 for the default camera
    try:
        while True:
            success, frame = camera.read()  # Read the camera frame
            if not success:
                break
            else:
                ret, buffer = cv2.imencode('.jpg', frame)
                frame = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')  # Concatenate frame one by one and show result
    finally:
        camera.release()

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/view_cam')
def view_cam():
    if session.get('username') != 'Admin':
        return redirect(url_for('login'))
    return render_template('view_cam.html')
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
