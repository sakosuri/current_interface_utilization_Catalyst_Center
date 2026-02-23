import requests
from requests.auth import HTTPBasicAuth
import yaml
import os
import openpyxl
import argparse
from datetime import datetime # Import datetime for timestamp

# Disable warnings for self-signed certificates (if applicable)
requests.packages.urllib3.disable_warnings()

# --- Configuration ---
# Generate a timestamp for the filename
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S") # Format: YYYYMMDD_HHMMSS
EXCEL_FILENAME = f"interface_utilization_report_{timestamp}.xlsx" # Name of the Excel file

# --- Configuration Loading Function ---
def load_config(config_file_path):
    """Loads configuration from a YAML file."""
    if not os.path.exists(config_file_path):
        raise FileNotFoundError(f"Configuration file '{config_file_path}' not found. Please create it.")
    try:
        with open(config_file_path, 'r') as f:
            config = yaml.safe_load(f)
        return config
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML configuration file: {e}")
    except Exception as e:
        raise Exception(f"An unexpected error occurred while loading config: {e}")

# --- API Interaction Functions ---
def get_token(catalyst_center_ip, username, password):
    """Obtains an authentication token from Cisco Catalyst Center."""
    url = f"https://{catalyst_center_ip}/api/system/v1/auth/token"
    response = requests.post(url, auth=HTTPBasicAuth(username, password), verify=False)
    response.raise_for_status()
    return response.json()['Token']

def get_device_id(token, catalyst_center_ip, device_name):
    """Retrieves the device ID for a given device name."""
    url = f"https://{catalyst_center_ip}/dna/intent/api/v1/networkDevices"
    headers = {'X-Auth-Token': token}
    response = requests.get(url, headers=headers, verify=False)
    response.raise_for_status()
    devices = response.json().get('response', [])
    for device in devices:
        if device.get('hostname', '').lower() == device_name.lower():
            return device.get('id')
    return None

def get_interface_id_and_status(token, catalyst_center_ip, device_id, interface_name):
    """Retrieves the interface ID and operational status for a given interface name on a device."""
    # This endpoint '/interface/network-device/{device_id}/interface-name' with query_params 'name'
    # is often less reliable than fetching all interfaces for a device and filtering locally.
    # Reverting to the more robust approach used previously.
    query_params = {
        'name': interface_name
    }
    # Fetch all interfaces for the device
    url = f"https://{catalyst_center_ip}/dna/intent/api/v1/interface/network-device/{device_id}/interface-name"
    headers = {'X-Auth-Token': token}
    response = requests.get(url, headers=headers,  params=query_params, verify=False)
    response.raise_for_status()
    iface = response.json().get('response', [])
    return iface.get('instanceUuid'), iface.get('status') # 'instanceUuid' is the ID, 'status' is operStatus


def get_interface_utilization(token, catalyst_center_ip, interface_id):
    """Retrieves Rx and Tx utilization for a given interface ID."""
    url = f"https://{catalyst_center_ip}/dna/data/api/v1/interfaces/{interface_id}"
    params = {
        'view': 'statistics'
    }
    headers = {
        'X-Auth-Token': token,
        'Content-Type': 'application/json'
    }
    response = requests.get(url, headers=headers, params=params, verify=False)
    response.raise_for_status()
    data = response.json()

    tx_util = None
    rx_util = None

    if 'response' in data and isinstance(data.get('response'), dict) and len(data.get('response')) > 0:
        interface_stats = data.get('response')
        tx_util = interface_stats.get('txUtilization')
        rx_util = interface_stats.get('rxUtilization')
    return tx_util, rx_util

def initialize_excel_report(filename):
    """
    Initializes a NEW Excel workbook with headers.
    This function will always create a new file, overwriting any existing one.
    """
    headers = ["DNA Center", "Device Name", "Interface Name", "Interface Status", "Tx Utilization (%)", "Rx Utilization (%)"]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Interface Utilization"
    sheet.append(headers)
    workbook.save(filename)
    print(f"Created a new Excel report: {filename}")

def append_to_excel_report(data_row, filename):
    """Appends a single row of data to the Excel report."""
    try:
        workbook = openpyxl.load_workbook(filename)
        sheet = workbook.active
        sheet.append(data_row)
        workbook.save(filename)
    except Exception as e:
        print(f"Error appending data to Excel file {filename}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Fetch Cisco Catalyst Center interface utilization and export to Excel.")
    parser.add_argument("--config", default="INT UTILIZATION.yaml", help="Path to the YAML configuration file.")
    args = parser.parse_args()

    try:
        config = load_config(args.config)

        # Create a dictionary for quick lookup of DNA Center details by name
        dna_centers_config = {dc['name']: dc for dc in config.get('dna_centers', [])}
        targets = config.get('targets', [])

        if not dna_centers_config:
            print("Error: No 'dna_centers' defined in config.yaml. Please define at least one DNA Center.")
            return
        if not targets:
            print("Warning: No 'targets' defined in config.yaml. Nothing to process.")
            return

        # Initialize Excel report - this will now always create a new one with a unique name
        initialize_excel_report(EXCEL_FILENAME)

        # Token caching to avoid re-authenticating repeatedly for the same DNA Center
        token_cache = {}

        # Iterate through each target group defined in the YAML
        for target_group in targets:
            dna_center_name = target_group.get('dna_center_name')
            devices_to_process = target_group.get('devices', [])

            if not dna_center_name:
                print(f"Skipping target group due to missing 'dna_center_name': {target_group}")
                continue

            dna_center_details = dna_centers_config.get(dna_center_name)
            if not dna_center_details:
                print(f"Error: DNA Center '{dna_center_name}' not found in 'dna_centers' configuration for target group. Skipping.")
                continue

            CATALYST_CENTER_IP = dna_center_details['ip']
            USERNAME = dna_center_details['username']
            PASSWORD = dna_center_details['password']

            # Get token once per DNA Center, or use cached token
            if CATALYST_CENTER_IP not in token_cache:
                try:
                    token = get_token(CATALYST_CENTER_IP, USERNAME, PASSWORD)
                    token_cache[CATALYST_CENTER_IP] = token
                    print(f"\n--- Obtained token for DNA Center: '{dna_center_name}' ({CATALYST_CENTER_IP}) ---")
                except requests.exceptions.RequestException as e:
                    print(f"Error getting token for DNA Center '{dna_center_name}': {e}")
                    continue
            else:
                token = token_cache[CATALYST_CENTER_IP]
                print(f"\n--- Using cached token for DNA Center: '{dna_center_name}' ({CATALYST_CENTER_IP}) ---")

            # Iterate through each device within the current target group
            for device_entry in devices_to_process:
                device_name = device_entry.get('device_name')
                interfaces_to_process = device_entry.get('interfaces')

                if not device_name:
                    print(f"Skipping device entry due to missing 'device_name': {device_entry}")
                    continue

                print(f"\nProcessing Device: '{device_name}' on DNA Center: '{dna_center_name}'")

                try:
                    device_id = get_device_id(token, CATALYST_CENTER_IP, device_name)
                    if not device_id:
                        print(f"Error: Device '{device_name}' not found on DNA Center '{dna_center_name}'. Skipping its interfaces.")
                        # Log error to Excel
                        excel_data = [
                            dna_center_name,
                            device_name,
                            "N/A",
                            "Device Not Found",
                            "N/A",
                            "N/A"
                        ]
                        append_to_excel_report(excel_data, EXCEL_FILENAME)
                        continue
                    print(f"Device ID for '{device_name}': {device_id}")

                    # Iterate through each interface for the current device
                    for interface_name in interfaces_to_process:
                        print(f"  Querying Interface: '{interface_name}'")
                        interface_id, oper_status = get_interface_id_and_status(token, CATALYST_CENTER_IP, device_id, interface_name)
                        if not interface_id:
                            print(f"  Error: Interface '{interface_name}' not found on device '{device_name}'.")
                            # Append a row indicating the interface was not found
                            excel_data = [
                                dna_center_name,
                                device_name,
                                interface_name,
                                "Interface Not Found",
                                "N/A",
                                "N/A"
                            ]
                            append_to_excel_report(excel_data, EXCEL_FILENAME)
                            continue

                        print(f"    Interface ID: {interface_id}")
                        print(f"    Interface Operational Status: {oper_status}")

                        tx_utilization, rx_utilization = get_interface_utilization(token, CATALYST_CENTER_IP, interface_id)
                        print(f"    Tx utilization: {tx_utilization}")
                        print(f"    Rx utilization: {rx_utilization}")

                        # Prepare data for Excel
                        excel_data = [
                            dna_center_name,
                            device_name,
                            interface_name,
                            oper_status,
                            tx_utilization,
                            rx_utilization
                        ]

                        # Export to Excel
                        append_to_excel_report(excel_data, EXCEL_FILENAME)

                except requests.exceptions.RequestException as e:
                    print(f"Network or API Error for device '{device_name}' on '{dna_center_name}': {e}")
                    if e.response is not None:
                        print(f"    Response Status Code: {e.response.status_code}")
                        print(f"    Response Body: {e.response.text}")
                    # Log error to Excel as well
                    excel_data = [
                        dna_center_name,
                        device_name,
                        "N/A", # Interface name not known at this point of error
                        f"API Error: {e}",
                        "N/A",
                        "N/A"
                    ]
                    append_to_excel_report(excel_data, EXCEL_FILENAME)
                except Exception as e:
                    print(f"An unexpected error occurred for device '{device_name}' on '{dna_center_name}': {e}")
                    # Log error to Excel as well
                    excel_data = [
                        dna_center_name,
                        device_name,
                        "N/A", # Interface name not known at this point of error
                        f"Unexpected Error: {e}",
                        "N/A",
                        "N/A"
                    ]
                    append_to_excel_report(excel_data, EXCEL_FILENAME)

    except FileNotFoundError as e:
        print(f"Configuration Error: {e}")
    except ValueError as e:
        print(f"Configuration Error: {e}")
    except KeyError as e:
        print(f"Configuration Error: Missing key in config.yaml: {e}. Please ensure all required fields are present.")
    except Exception as e:
        print(f"An unexpected error occurred during script execution: {e}")

if __name__ == "__main__":
    main()
