import re
def https_converter(url):
    # Check if the URL starts with "hxxp://" (case insensitive)
        if url.lower().startswith("hxxp://"):
            # Replace "hxxp://" with "http://"
            return "http://" + url[7:]
        # Check if the URL starts with "hxxps://" (case insensitive)
        elif url.lower().startswith("hxxps://"):
            # Replace "hxxps://" with "https://"
            return "https://" + url[8:]
        
        return url

text = "@CertiKAlert,#CertiKSkynetAlert 🚨  Beware of a fake Arbitrum airdrop posted on social media   Do not interact with hxxps://arb.base-eth.net/?invite=twitter  Stay vigilant! https://t.co/TqRAR3nrbi,2023-10-10T23:48:58.000Z"
url_pattern = re.compile(r'hxxp\S+')
result = re.findall(url_pattern, text)
for link in result:
     result2 = https_converter(link)
     print(result2)



text2 = "victim: 0xa0d8f53e1a754b766f7a1498762f7d9f66734985  scammer: 0x808f9ccbf3f6cb5aeb5fa104bf87cbecc1d168b0 0x29488e5fd6bf9b3cc98a9d06a25204947cccbe4d,2023-09-17T01:40:52.000Z,https://twitter.com/realScamSniffer/status/1703222454263693738"
def extract_addresses(text):
    # Find all 0x addresses
    all_addresses = re.findall(r'\b0x[a-fA-F0-9]{40}\b', text)

    # Filter out addresses preceded by 'victim' in various formats
    valid_addresses = []
    for address in all_addresses:
        # Search for 'victim' pattern ending with the address in the text
        if not re.search(r'\b(?i)victim\s*:?\s*' + re.escape(address), text):
            valid_addresses.append(address)

    return valid_addresses

# Sample text
sample_text = "Address: 0x1234567890abcdef1234567890abcdef12345678, Victim: 0xabcdef1234567890abcdef1234567890abcdef12, victim : 0xabcdef1234567890abcdef1234567890abcdef34"

# Extract addresses
addresses = extract_addresses(sample_text)
print(addresses)


result =  extract_addresses(text2)
print(result)