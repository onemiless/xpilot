"""EXPERIMENT: build a bounded C3-only VPN config from an untrusted input.

This does not launch a core or configure routes. It deliberately rejects proxy
providers and unsupported node options rather than passing them to a root helper.
"""

NODE_KEYS = {
  'ss': {'name','type','server','port','cipher','password','udp'},
  'ssr': {'name','type','server','port','cipher','password','udp','protocol','protocol-param','obfs','obfs-param','group','remarks'},
  'vmess': {'name','type','server','port','uuid','alterId','cipher','udp','tls','servername','network','ws-opts','client-fingerprint'},
  'vless': {'name','type','server','port','uuid','udp','tls','servername','network','ws-opts','client-fingerprint','flow','reality-opts'},
  'trojan': {'name','type','server','port','password','udp','sni','servername','network','ws-opts','client-fingerprint'},
}


def valid_value(value, depth=0):
  if depth > 4:
    return False
  if value is None or type(value) in (bool, int):
    return True
  if isinstance(value, str):
    return len(value) <= 4096 and '\x00' not in value
  if type(value) is list:
    return len(value) <= 128 and all(valid_value(item, depth+1) for item in value)
  if type(value) is dict:
    return len(value) <= 64 and all(isinstance(k, str) and len(k) <= 128 and valid_value(v, depth+1) for k,v in value.items())
  return False


def build_config(raw, secret):
  if type(raw) is not dict or not isinstance(secret, str) or not 1 <= len(secret) <= 256:
    raise ValueError('invalid input')
  if raw.get('proxy-providers'):
    raise ValueError('proxy providers are not supported by this candidate')
  nodes=raw.get('proxies')
  if type(nodes) is not list or not 1 <= len(nodes) <= 1000:
    raise ValueError('invalid nodes')
  names=set()
  for node in nodes:
    if type(node) is not dict or node.get('type') not in NODE_KEYS:
      raise ValueError('unsupported node type')
    if set(node) - NODE_KEYS[node['type']] or not valid_value(node):
      raise ValueError('unsupported node option')
    name=node.get('name'); server=node.get('server'); port=node.get('port')
    if not isinstance(name,str) or not name or len(name)>256 or name in names:
      raise ValueError('invalid node name')
    if not isinstance(server,str) or not server or len(server)>253 or type(port) is not int or not 1 <= port <= 65535:
      raise ValueError('invalid node endpoint')
    if node.get('network', 'tcp') not in ('tcp', 'ws'):
      raise ValueError('unsupported network transport')
    reality=node.get('reality-opts', {})
    if type(reality) is not dict or set(reality) - {'public-key','short-id'} or not all(isinstance(v,str) for v in reality.values()):
      raise ValueError('invalid reality options')
    ws=node.get('ws-opts', {})
    if type(ws) is not dict or set(ws) - {'path','headers','max-early-data','early-data-header-name'}:
      raise ValueError('invalid websocket options')
    headers=ws.get('headers', {})
    if type(headers) is not dict or not all(isinstance(k,str) and isinstance(v,str) for k,v in headers.items()):
      raise ValueError('invalid websocket headers')
    if 'path' in ws and not isinstance(ws['path'],str):
      raise ValueError('invalid websocket path')
    if 'max-early-data' in ws and (type(ws['max-early-data']) is not int or not 0 <= ws['max-early-data'] <= 65535):
      raise ValueError('invalid early data limit')
    if 'early-data-header-name' in ws and not isinstance(ws['early-data-header-name'],str):
      raise ValueError('invalid early data header')
    names.add(name)
  return {
    'proxies':nodes,'proxy-groups':[{'name':'C3-PROXY','type':'select','proxies':[n['name'] for n in nodes]}],
    'rules':['IP-CIDR,127.0.0.0/8,DIRECT,no-resolve','IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
             'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve','IP-CIDR,172.16.0.0/12,DIRECT,no-resolve','MATCH,C3-PROXY'],
    'mixed-port':17890,'port':0,'socks-port':0,'redir-port':0,'tproxy-port':0,
    'allow-lan':False,'bind-address':'127.0.0.1','external-controller':'127.0.0.1:17891','external-ui':'',
    'secret':secret,'tun':{'enable':True,'device':'c3mh0','stack':'gvisor','auto-route':False,'auto-detect-interface':False,'mtu':1500},
    'dns':{'enable':True,'listen':'127.0.0.1:17893','enhanced-mode':'redir-host','default-nameserver':['223.5.5.5','119.29.29.29'],
           'proxy-server-nameserver':['223.5.5.5','119.29.29.29'],'nameserver':['https://1.1.1.1/dns-query#C3-PROXY']},
    'ipv6':False,'log-level':'warning','find-process-mode':'off','mode':'global',
  }
