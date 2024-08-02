from flask import Flask, request, redirect, url_for, send_from_directory, render_template, session
from flask_bcrypt import Bcrypt
import os
import shutil
import logging

app = Flask(__name__)
bcrypt = Bcrypt(app)

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 * 1024  # 5 GB limit
app.secret_key = 'supersecretkey'  # Change this to a random secret key

# In-memory user storage. Replace with a proper database in production.
users = {}

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Set up logging
logging.basicConfig(filename='server.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and bcrypt.check_password_hash(users[username], password):
            session['logged_in'] = True
            session['username'] = username
            logging.info(f"User {username} logged in.")
            return redirect(url_for('index'))
        else:
            logging.warning(f"Failed login attempt for username: {username}")
            return "Invalid username or password!"
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users:
            logging.warning(f"Attempt to register with existing username: {username}")
            return "Username already exists!"
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        users[username] = hashed_password
        logging.info(f"User {username} registered successfully.")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/index', defaults={'path': ''})
@app.route('/index/<path:path>')
def index(path):
    if not session.get('logged_in'):
        logging.warning("Unauthorized access attempt to index.")
        return redirect(url_for('login'))
    current_path = os.path.join(app.config['UPLOAD_FOLDER'], path)
    if not os.path.exists(current_path):
        logging.error(f"Folder not found: {current_path}")
        return "Folder not found!", 404
    files = os.listdir(current_path)
    files = [{'name': f, 'is_dir': os.path.isdir(os.path.join(current_path, f))} for f in files]
    return render_template('index.html', files=files, path=path)

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
    for file in files:
        if file and file.filename != '':
            filepath = os.path.join(current_path, file.filename)
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

@app.route('/logout')
def logout():
    logging.info(f"User {session.get('username')} logged out.")
    session['logged_in'] = False
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True) 
