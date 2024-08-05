import json
import os

USER_DATA_FILE = 'users.json'

def update_existing_users():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r') as file:
            users = json.load(file)
        
        updated = False
        for user in users:
            if isinstance(users[user], dict):
                if 'suspended' not in users[user]:
                    users[user]['suspended'] = False
                    updated = True
            else:
                # This handles the case where the password was stored directly
                users[user] = {'password': users[user], 'suspended': False}
                updated = True
        
        if updated:
            with open(USER_DATA_FILE, 'w') as file:
                json.dump(users, file, indent=4)
            print("Users updated successfully.")
        else:
            print("No updates needed.")
    else:
        print("User data file not found.")

if __name__ == "__main__":
    update_existing_users()
