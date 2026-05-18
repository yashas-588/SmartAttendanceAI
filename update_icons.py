import glob
import os
import re

def replace_emojis(text):
    # Mapping of emojis to Lucide icons
    replacements = {
        "⚡ AttendAI": '<i data-lucide="zap" class="mr-2" style="display:inline-block; vertical-align:middle; width:20px; height:20px;"></i>AttendAI',
        "📊": '<i data-lucide="bar-chart-2"></i>',
        "🎥": '<i data-lucide="video"></i>',
        "👥": '<i data-lucide="users"></i>',
        "✏️": '<i data-lucide="edit-3"></i>',
        "📁": '<i data-lucide="folder"></i>',
        "⚙️": '<i data-lucide="settings"></i>',
        "🎯": '<i data-lucide="target"></i>',
        "🤖": '<i data-lucide="bot"></i>',
        "📡": '<i data-lucide="radio"></i>',
        "☁️": '<i data-lucide="cloud"></i>',
        "🛡️": '<i data-lucide="shield"></i>',
        "🔔": '<i data-lucide="bell"></i>',
        "✅": '<i data-lucide="check-circle"></i>',
        "❌": '<i data-lucide="x-circle"></i>',
        "🚫": '<i data-lucide="ban"></i>',
        "📈": '<i data-lucide="trending-up"></i>',
        "🔴": '<i data-lucide="circle-dot"></i>',
        "🔍": '<i data-lucide="search"></i>',
        "⏸": '<i data-lucide="pause"></i>',
        "▶️": '<i data-lucide="play"></i>',
        "📋": '<i data-lucide="clipboard-list"></i>',
        "👁️": '<i data-lucide="eye"></i>',
        "📷": '<i data-lucide="camera"></i>',
        "📅": '<i data-lucide="calendar"></i>',
        "➕": '<i data-lucide="plus"></i>',
        "📸": '<i data-lucide="camera"></i>',
        "🗑️": '<i data-lucide="trash-2"></i>',
        "📉": '<i data-lucide="trending-down"></i>',
        "⬇": '<i data-lucide="download"></i>',
        "👨‍💻": '<i data-lucide="code"></i>',
        "👨‍🎨": '<i data-lucide="pen-tool"></i>',
        "👩‍💼": '<i data-lucide="briefcase"></i>',
        "👨‍🔬": '<i data-lucide="flask-conical"></i>',
        "🎉": '<i data-lucide="party-popper"></i>',
        "ℹ️": '<i data-lucide="info"></i>',
        "⚠️": '<i data-lucide="alert-triangle"></i>'
    }
    
    for emoji, icon in replacements.items():
        text = text.replace(emoji, icon)
        
    # Add Lucide script if not present
    if "lucide@latest" not in text:
        text = text.replace("</head>", '  <script src="https://unpkg.com/lucide@latest"></script>\n</head>')
        text = text.replace("</body>", '  <script>lucide.createIcons();</script>\n</body>')
        
    return text

for filepath in glob.glob('web/*.html'):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    updated_content = replace_emojis(content)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(updated_content)

print("Icons updated successfully!")
