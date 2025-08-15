import requests
import aiohttp
import json
from config import Config

# Assuming you've installed aiohttp: pip install aiohttp


# Making the function async is the best practice for API calls
async def call_gemini_api(prompt, model="gemini-2.0-flash-lite"):
    """
    Asynchronously calls the Google Gemini API with the given prompt.
    Uses aiohttp for non-blocking I/O and gets token count in a single call.
    """
    if not Config.GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY is not set in config.")
        return None

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={Config.GEMINI_API_KEY}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "topP": 0.95,
            "topK": 64,
            "maxOutputTokens": 8192,
            "responseMimeType": "text/plain"
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
    }

    try:
        # Use an async HTTP client (aiohttp) and a context manager
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload) as response:
                response.raise_for_status() # Raises an exception for bad status codes
                response_data = await response.json()
                
                # Get the token count directly from the response
                # The usageMetadata field contains the token counts
                usage = response_data.get('usageMetadata', {})
                prompt_token_count = usage.get('promptTokenCount', 0)
                print(f"The prompt has {prompt_token_count} tokens.")

                if response_data and response_data.get('candidates'):
                    return response_data['candidates'][0]['content']['parts'][0]['text']
                else:
                    print(f"Gemini API response did not contain expected content: {response_data}")
                    return None
    except aiohttp.ClientError as e:
        print(f"Error calling Gemini API: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

# def call_gemini_api(prompt, model="gemini-2.0-flash"):
#     """
#     Calls the Google Gemini API with the given prompt.
#     """
#     if not Config.GEMINI_API_KEY:
#         print("Error: GEMINI_API_KEY is not set in config.")
#         return None

#     api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={Config.GEMINI_API_KEY}"
#     headers = {'Content-Type': 'application/json'}
#     payload = {
#         "contents": [{"parts": [{"text": prompt}]}],
#         "generationConfig": {
#             "temperature": 0.7,
#             "topP": 0.95,
#             "topK": 64,
#             "maxOutputTokens": 8192,
#             "responseMimeType": "text/plain"
#         },
#         "safetySettings": [
#             {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
#             {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
#             {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
#             {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
#         ]
#     }

#     # --- Code to count tokens ---
#     print("Counting tokens...")

#     # 1. Define the URL for the countTokens endpoint.
#     # It uses the same model as your generateContent request.
#     count_tokens_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens?key={Config.GEMINI_API_KEY}"

#     # 2. Create the payload for the countTokens request.
#     # This payload only needs the "contents" from your original payload.
#     count_tokens_payload = {
#         "contents": payload["contents"]
#     }

#     try:
#         response = requests.post(api_url, headers=headers, json=payload)
#         response.raise_for_status()
#         response_data = response.json()

#         count_token_response = requests.post(count_tokens_url, headers=headers, json=count_tokens_payload)
#         count_token_data = count_token_response.json()
#         token_count = count_token_data.get("totalTokens", 0)

#         print(f"The prompt has {token_count} tokens.")
        
#         if response_data and response_data.get('candidates'):
#             return response_data['candidates'][0]['content']['parts'][0]['text']
#         else:
#             print(f"Gemini API response did not contain expected content: {response_data}")
#             return None
#     except requests.exceptions.RequestException as e:
#         print(f"Error calling Gemini API: {e}. Response: {e.response.text if e.response else 'N/A'}")
#         return None
#     except Exception as e:
#         print(f"An unexpected error occurred while processing Gemini API response: {e}")
#         return None
    

async def call_gemini_api_structured(prompt, response_schema, model="gemini-2.5-flash"):
    """
    Calls the Google Gemini API with the given prompt, requesting structured JSON output.
    """
    if not Config.GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY is not set in config.")
        return None

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={Config.GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.95,
            "topK": 64,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json", # Request JSON output
            "responseSchema": response_schema # Specify the desired JSON schema
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
    }

    try:
        # response = requests.post(api_url, headers=headers, json=payload)
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                response.raise_for_status()
                response_data =await response.json()

                usage = response_data.get('usageMetadata', {})
                prompt_token_count = usage.get('promptTokenCount', 0)
                print(f"The prompt has {prompt_token_count} tokens.")
                
                if response_data and response_data.get('candidates'):
                    # The structured response is in a 'text' part, which is a JSON string
                    json_string = response_data['candidates'][0]['content']['parts'][0]['text']
                    return json.loads(json_string) # Parse the JSON string into a Python dict
                else:
                    print(f"Gemini API structured response did not contain expected content: {response_data}")
                    return None
    except requests.exceptions.RequestException as e:
        print(f"Error calling structured Gemini API: {e}. Response: {e.response.text if e.response else 'N/A'}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from structured Gemini API response: {e}. Raw response: {response_data}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while processing structured Gemini API response: {e}")
        return None


