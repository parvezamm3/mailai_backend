import base64
import datetime
import uuid
import pytz

def decode_conversation_index(b64_index):
    """
    Decodes the Outlook conversationIndex from a Base64 string to a
    more readable, hierarchical format.

    Args:
        b64_index (str): The Base64-encoded conversationIndex string.

    Returns:
        dict: A dictionary representing the decoded conversation index,
              including timestamps and the conversation GUID.
    """
    try:
        binary_data = base64.b64decode(b64_index)
    except (ValueError, TypeError) as e:
        print(f"Error decoding Base64 string: {e}")
        return {}

    # A conversation index must be at least 22 bytes long (the header).
    if len(binary_data) < 22:
        print("Invalid conversationIndex: too short.")
        return {}

    # The header is 22 bytes.
    # Byte 0: Reserved (usually 1)
    # Bytes 1-5: The 5-byte timestamp for the conversation's creation.
    # Bytes 6-21: The 16-byte GUID for the conversation.
    reserved_byte = binary_data[0]
    timestamp_bytes = binary_data[1:6]
    guid_bytes = binary_data[6:22]

    # Convert the 5-byte timestamp to a datetime object.
    # This requires padding with zeros to make it a valid 8-byte FILETIME.
    # FILETIME is the number of 100-nanosecond intervals since Jan 1, 1601 UTC.
    padded_timestamp = b'\x00\x00\x00' + timestamp_bytes[::-1]  # Pad and reverse bytes
    filetime_value = int.from_bytes(padded_timestamp, byteorder='big')
    original_timestamp = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc) + \
                         datetime.timedelta(microseconds=filetime_value // 10)

    # Convert the 16-byte GUID to a readable UUID string.
    conversation_guid = str(uuid.UUID(bytes=guid_bytes))

    # Parse any child blocks. Each child block is 5 bytes.
    child_blocks = []
    if len(binary_data) > 22:
        child_data = binary_data[22:]
        if len(child_data) % 5 != 0:
            print("Warning: Child blocks are not a multiple of 5 bytes.")
        
        for i in range(0, len(child_data), 5):
            block = child_data[i:i+5]
            if len(block) == 5:
                # The first bit of the first byte is a flag. The remaining 31 bits
                # represent a time delta. The last byte is an increment.
                time_delta_raw = int.from_bytes(block[:4], byteorder='big')
                child_blocks.append({
                    "timestamp_delta": time_delta_raw,
                    "increment": block[4]
                })

    return {
        "original_timestamp": original_timestamp.isoformat(),
        "conversation_guid": conversation_guid,
        "child_blocks": child_blocks,
        "number of replies":len(child_blocks),
        "raw_base64": b64_index
    }


def convert_utc_to_local(utc_datetime_string: str):
    """
    Converts a UTC datetime string (ISO 8601 format) to a local datetime object.

    Args:
        utc_datetime_string: A string representing a UTC date and time,
                             e.g., '2025-08-08T10:31:00Z'.

    Returns:
        A timezone-aware datetime object in the local system's time zone.
    """
    try:
        utc_datetime = datetime.datetime.fromisoformat(utc_datetime_string)
        original_tz = pytz.timezone('Etc/GMT-9')
        aware_time = utc_datetime.astimezone(original_tz)
        local_datetime = aware_time.strftime("%Y-%m-%d %H:%M:%S")

        return local_datetime
    except ValueError as e:
        print(f"Error parsing datetime string: {e}")
        return None
