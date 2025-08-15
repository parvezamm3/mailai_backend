import base64
from bs4 import BeautifulSoup
import re
import io
from PyPDF2 import PdfReader # pip install pypdf2
from docx import Document # pip install python-docx

patterns = [
        re.compile(r'[-—・]{4,}'),      # Matches 4 or more dashes, em dashes, or interpuncts
        re.compile(r'(\.{3,}\-){2,}'),  # Matches sequences like ...- that repeat two or more times
        re.compile(r'(\*){4,}'),        # Matches 4 or more asterisks
        re.compile(r'(\s*◆◇\s*){4,}'), # Matches the ◆◇ sequence with optional whitespace, repeated 4+ times
        re.compile(r'(\s*＿/\s*){4,}'), # Matches the ＿/ sequence with optional whitespace, repeated 4+ times
        re.compile(r'(\s*・～\s*){4,}'), # Matches the ・～ sequence with optional whitespace, repeated 4+ times
    ]

def get_unique_body_outlook(body_content):
    body_plain = body_content.get('content', 'No body available.')
    cleaned_body = ""
    if body_content.get('contentType') == 'html':
        soup = BeautifulSoup(body_plain, 'html.parser')
        body_plain = soup.get_text().splitlines()
        non_empty_lines = [line for line in body_plain if line.strip()]
        cleaned_body = "\n".join(non_empty_lines)
    return cleaned_body

def get_inline_attachments_outlook(body_content):
    body_plain = body_content.get('content', 'No body available.')
    cid_references = set()
    if body_content.get('contentType') == 'html':
        soup = BeautifulSoup(body_plain, 'html.parser')
        cid_pattern = re.compile(r'src="cid:(.*?)"')
        cid_references = set(cid_pattern.findall(str(soup)))
    return cid_references


def extract_email_thread_outlook(body_content, delim1, delim2):
    pattern = re.compile(f"({re.escape(delim1)}|{re.escape(delim2)})")
    body_plain = body_content.get('content', 'No body available.')
    if body_content.get('contentType') == 'html':
        soup = BeautifulSoup(body_plain, 'html.parser')
        # m = pattern.search(str(soup))
        # if m:
        #     sp = m.start()
        #     dl = len(m.group(0))
        #     spm = [str(soup)[:sp], str(soup)[sp + dl:]]
        #     ns = BeautifulSoup(spm[0], 'html.parser')
        #     cid_pattern = re.compile(r'src="cid:(.*?)"')

        #     # Find all matches in the HTML body
        #     cid_references = cid_pattern.findall(spm[0])

        #     # print("Found CID references:")
        #     for cid in cid_references:
        #         print(f"- {cid}")
        #         # print("Main Message ---------------")
        #         # print(ns.get_text())

        #     # print("NS------",ns )
        cid_pattern = re.compile(r'src="cid:(.*?)"')

        # Find all matches in the HTML body
        cid_references = set(cid_pattern.findall(str(soup)))

        # print("Found CID references:")
        for cid in cid_references:
            print(f"- {cid}")
        body_plain = soup.get_text().splitlines()
        non_empty_lines = [line for line in body_plain if line.strip()]
        print("Main body  :", "\n".join(non_empty_lines))
    # match = pattern.search(body_plain)
    main_body = "\n".join(non_empty_lines)
    return main_body

def extract_text_from_attachment(file_bytes, filename):
    """
    Extracts plain text from various attachment file types.
    """
    file_extension = filename.split('.')[-1].lower()
    
    if file_extension == 'txt':
        return file_bytes.decode('utf-8', errors='ignore')
    elif file_extension == 'pdf':
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        except Exception as e:
            print(f"Error extracting text from PDF {filename}: {e}")
            return None
    elif file_extension == 'docx':
        try:
            document = Document(io.BytesIO(file_bytes))
            return "\n".join([paragraph.text for paragraph in document.paragraphs])
        except Exception as e:
            print(f"Error extracting text from DOCX {filename}: {e}")
            return None
    # Add more handlers for other file types (e.g., xlsx, csv, images with OCR)
    # elif file_extension in ['jpg', 'jpeg', 'png']:
    #     try:
    #         image = Image.open(io.BytesIO(file_bytes))
    #         return pytesseract.image_to_string(image, lang='jpn+eng') # Specify languages for OCR
    #     except Exception as e:
    #         print(f"Error performing OCR on image {filename}: {e}")
    #         return None
    else:
        print(f"Unsupported file type for text extraction: {filename}")
        return None

def parse_email_body_and_attachments(message_payload, message_type):
    """
    Parses the full message payload to extract plain text body and attachment data.
    Handles both Gmail and Outlook payload structures.
    Returns (plain_body_content, attachments_data_list)
    """
    plain_body_content = "No body available."
    attachments_data = []

    if message_type == 'gmail_message_added':
        # Gmail payload structure: parts -> body -> data (base64url encoded)
        # Find the main text/plain part for the body
        def find_gmail_body_part(parts):
            print("Find Gmail body parts with attatchment")
            
            for part in parts:
                print("part",part)
                if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                    try:
                        return base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                    except Exception as e:
                        print(f"Error decoding Gmail text/plain part: {e}")
                if part.get('parts'): # Check for nested parts
                    nested_body = find_gmail_body_part(part['parts'])
                    if nested_body:
                        return nested_body
            return None
        
        # Extract attachments
        def extract_gmail_attachments(parts):
            print("Extracted attachments")
            extracted_attachments = []
            for part in parts:
                print("Filename Part", part)
                if part.get('filename') and part.get('body', {}).get('data'):
                    
                    try:
                        decoded_bytes = base64.urlsafe_b64decode(part['body']['data'])
                        extracted_attachments.append({
                            'id': part.get('partId'), # Gmail uses partId for attachments in payload
                            'name': part['filename'],
                            'contentType': part.get('mimeType'),
                            'size': part.get('body', {}).get('size'),
                            'isInline': 'Content-ID' in [h['name'] for h in part.get('headers', [])], # Heuristic for inline
                            'contentBytes': decoded_bytes # Store raw bytes, not base64 string
                        })
                        print(extracted_attachments)
                    except Exception as e:
                        print(f"Error processing Gmail attachment {part.get('filename')}: {e}")
                if part.get('parts'): # Recursively check nested parts
                    extracted_attachments.extend(extract_gmail_attachments(part['parts']))
            return extracted_attachments

        # Extract main body
        if message_payload.get('payload', {}).get('parts'):
            print("With Parts")
            plain_body_content = find_gmail_body_part(message_payload['payload']['parts']) or plain_body_content
            print("Plain Body Content", plain_body_content)
            attachments_data = extract_gmail_attachments(message_payload['payload']['parts'])
            print("Attatchment Data", attachments_data)
        elif message_payload.get('payload', {}).get('body', {}).get('data'): # Simple case, no parts
            try:
                print("Without parts")
                plain_body_content = base64.urlsafe_b64decode(message_payload['payload']['body']['data']).decode('utf-8', errors='ignore')
                print("Plain Body Content",plain_body_content)
            except Exception as e:
                print(f"Error decoding simple Gmail body: {e}")

    elif message_type in ['outlook_message_added', 'outlook_received_mail', 'outlook_sent_mail']:
        # Outlook Graph API message payload structure
        body_content = message_payload.get('body', {})
        plain_body_content = body_content.get('content', 'No body available.')
        if body_content.get('contentType') == 'html':
            try:
                soup = BeautifulSoup(plain_body_content, 'html.parser')
                plain_body_content = soup.get_text()
            except Exception as parse_error:
                print(f"Error parsing HTML body for Outlook: {parse_error}")
        
        # Attachments are in a separate 'attachments' array at the top level of the message payload
        attachments = message_payload.get('attachments', [])
        for attach in attachments:
            if attach.get('name') and attach.get('contentBytes'):
                try:
                    # Outlook attachment contentBytes is standard base64 encoded
                    decoded_bytes = base64.b64decode(attach['contentBytes'])
                    attachments_data.append({
                        'id': attach.get('id'),
                        'name': attach['name'],
                        'contentType': attach.get('contentType'),
                        'size': attach.get('size'),
                        'isInline': attach.get('isInline', False),
                        'contentBytes': decoded_bytes # Store raw bytes
                    })
                except Exception as e:
                    print(f"Error processing Outlook attachment {attach.get('name')}: {e}")
    
    return plain_body_content, attachments_data

def extract_email_thread(body_plain, *separators):
    """
    Extracts the current message and previous conversation history from a plain text email body.
    It splits the body based on common email client reply separators.
    Returns a tuple: (current_message, previous_history_string)
    """
    # Common separators used by email clients
    default_separators = [
        "-----Original Message-----",
        "From:", # Often precedes quoted history
        "On ", # "On [Date], [Sender] wrote:"
        "Sent from my iPhone", # Common mobile signature, often at end of current message
        "Sent from my Android",
        "Sent from my Samsung device",
        "________________________________",
        "--- Forwarded message ---",
        "---------- Forwarded message ---------"
    ]
    all_separators = list(separators) + default_separators

    current_message_parts = []
    history_parts = []
    in_history = False

    lines = body_plain.splitlines()
    for line in lines:
        is_separator = False
        for sep in all_separators:
            if line.strip().startswith(sep):
                is_separator = True
                in_history = True
                break
        
        if is_separator:
            history_parts.append(line) # Include the separator in history
        elif in_history:
            history_parts.append(line)
        else:
            current_message_parts.append(line)
    
    current_message = "\n".join(current_message_parts).strip()
    previous_history = "\n".join(history_parts).strip()

    # If no clear separator, the whole body is considered the current message
    if not previous_history and current_message == body_plain.strip():
        return body_plain.strip(), ""
    
    return current_message, previous_history

