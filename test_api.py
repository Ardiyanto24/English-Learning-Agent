import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()
response = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=50, messages=[{"role": "user", "content": "Say: API connected successfully"}])
print(response.content[0].text)
