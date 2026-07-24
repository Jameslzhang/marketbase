import requests, json

url = 'https://data.eastmoney.com/dataapi/xuangu/list'
params = {
    'st': 'SECURITY_CODE', 'sr': '1', 'ps': '5', 'p': '1',
    'sty': 'SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,INDUSTRY,CONCEPT,BOARD_NAME',
    'filter': '(MARKET+in+("上交所主板","深交所主板"))',
    'source': 'SELECT_SECURITIES', 'client': 'WEB',
}
headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/xuangu/'}
r = requests.get(url, params=params, headers=headers, timeout=30)
data = r.json()
if data.get('success'):
    items = data['result']['data']
    print(f'Total: {data["result"]["count"]}')
    for item in items[:3]:
        print(json.dumps(item, ensure_ascii=False, indent=2))
else:
    print('Failed:', json.dumps(data, ensure_ascii=False))