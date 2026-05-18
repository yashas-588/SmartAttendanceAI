import glob
import re

emoji_pattern = re.compile(
    "["
    u"\U0001f600-\U0001f64f"  # emoticons
    u"\U0001f300-\U0001f5ff"  # symbols & pictographs
    u"\U0001f680-\U0001f6ff"  # transport & map symbols
    u"\U0001f1e0-\U0001f1ff"  # flags (iOS)
    u"\u2702-\u27b0"          # Dingbats
    u"\u24C2-\u1F251"
    "]+", flags=re.UNICODE)

for filepath in glob.glob('web/*.html'):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    matches = emoji_pattern.findall(content)
    if matches:
        print(f"File: {filepath} -> Emojis found: {matches}")
