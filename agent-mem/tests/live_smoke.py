import json
import time
import urllib.request

base = 'http://localhost:8000'
user = 'memory-tool-smoke-' + str(int(time.time()))

def request(path, data=None, method=None):
    req = urllib.request.Request(base+path, data=json.dumps(data).encode() if data else None, headers={'Content-Type': 'application/json'}, method=method)
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)

print('health', request('/api/health'), flush=True)
results = []
for message in ['请简短解释 Python 列表与元组的区别。', '请记住，我叫测试小舟，最喜欢的饮料是桂花乌龙茶。', '请查一下我们之前聊过的记忆，我叫什么名字，最喜欢喝什么？']:
    result = request('/api/chat', {'user_id': user, 'message': message})
    print(json.dumps({'message':message, **result}, ensure_ascii=False), flush=True)
    results.append(result)
assert not results[0]['memory_searches'], 'General question unexpectedly searched'
assert results[1]['memory_write']['status'] == 'saved', 'Preference was not persisted'
assert results[2]['memory_searches'], 'Historical question did not search'
assert any('小舟' in text for text in results[2]['recalled_memories']), 'Saved name not recalled'
assert '小舟' in results[2]['answer'] and '乌龙' in results[2]['answer']
stored = request('/api/memories?user_id='+user)['memories']
assert any('小舟' in item['memory'] for item in stored), 'Memory not in storage'
isolated = request('/api/memories?user_id='+user+'-other')['memories']
assert not isolated, 'Identity isolation failed'
print('LIVE SMOKE PASS', user, flush=True)
request('/api/memories?user_id='+user, method='DELETE')
