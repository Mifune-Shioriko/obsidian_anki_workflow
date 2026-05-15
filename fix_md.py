import re

with open('/home/shioriko/Share/Document/Note7/Inbox/GP2_HW3_Study_Guide.md', 'r') as f:
    text = f.read()

# The incorrect regexes added by me earlier:
# text = re.sub(r'([a-zA-Z0-9])\$', r'\1 $', text)
# text = re.sub(r'\$([a-zA-Z0-9])', r'$ \1', text)

# To reverse it, or just clean up inline math spaces:
# Find all $...$ that are not $$...$$
# Replaces $ <content> $ with $<content>$
def clean_math(match):
    content = match.group(1).strip()
    return f"${content}$"

# Negative lookbehind and lookahead to avoid $$ block math
text = re.sub(r'(?<!\$)\$([^\$]+?)\$(?!\$)', clean_math, text)

with open('/tmp/fixed.md', 'w') as f:
    f.write(text)

