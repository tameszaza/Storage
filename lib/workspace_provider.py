"""Pure inference adapters: providers never execute Workspace tools."""
import base64
import http.client
import io
import json
import copy

from flask import current_app


class Unavailable(RuntimeError):
    def __init__(self,message,retry_after=30,code='provider-unavailable'):
        super().__init__(message);self.retry_after=max(5,min(3600,int(retry_after)));self.code=code
class InvalidOutput(ValueError): pass


def compact(context,instruction,schema):
    """Preserve the current instruction; trim old evidence before a small model's window fills."""
    value=dict(context)
    def size():return len(json.dumps(value,ensure_ascii=False).encode())+len(instruction.encode())+len(json.dumps(schema).encode())
    if size()<=22000:return value
    if 'available_tools' in value:
        value['available_tools']=[{**v,'description':v.get('description','')[:150]} for v in value['available_tools']]
    if 'selected_file_context' in value:value['selected_file_context']=value['selected_file_context'][:2400]+'\n[Context shortened; read the file for more.]'
    for key in ('recent_conversation','recent_results','observations','actual_results'):
        if key in value:
            value[key]=value[key][-2:]
            value[key]=[dict(v,**({'result':{'excerpt':json.dumps(v['result'],ensure_ascii=False)[:1200],'truncated':True}} if len(json.dumps(v.get('result',{}),ensure_ascii=False))>1200 else {})) for v in value[key]]
    if 'observed_references' in value:value['observed_references']=value['observed_references'][-30:]
    if size()>22000:
        for key in ('recent_conversation','earlier_observations','selected_file_context'):value.pop(key,None)
        if 'observed_references' in value:value['observed_references']=value['observed_references'][-12:]
    if size()>22000:raise InvalidOutput('The request and required evidence exceed the model context budget; use a shorter request')
    return value


def image_bytes(raw):
    from lib.assistant_images import open_images
    with open_images(raw) as images:
        image = images[0].convert('RGB')
        try:
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=85)
            if output.tell() > 1400000:
                image.thumbnail((1536, 1536))
                output = io.BytesIO(); image.save(output, format='JPEG', quality=70)
            return output.getvalue()
        finally: image.close()


def generate(provider, instruction, context, schema, image=None):
    context=compact(context,instruction,schema)
    if provider == 'cyborg':
        body = dict(action='workspace',model='gemma4:12b-it-qat', instruction=instruction, context={**context,'output_schema':schema}, schema=schema)
        if image: body['images'] = [base64.b64encode(image_bytes(image)).decode()]
        connection = http.client.HTTPConnection('100.66.32.22', 9019, timeout=3)
        try:
            connection.connect(); connection.sock.settimeout(245)
            connection.request('POST', '/v1/assist', json.dumps(body, ensure_ascii=False).encode(), {'Content-Type':'application/json'})
            response = connection.getresponse(); raw = response.read(256000)
            if response.status in (400,413,422): raise InvalidOutput('Cyborg rejected the bounded structured request')
            if response.status==503:
                try:busy=json.loads(raw).get('detail')=='Cyborg is checking another passage. Try again shortly.'
                except (ValueError,AttributeError):busy=False
                if busy:raise Unavailable('Cyborg is finishing another inference',retry_after=5,code='provider-busy')
            if response.status != 200: raise Unavailable('Cyborg is offline, paused or busy')
            return json.loads(raw)['result']
        except (OSError, http.client.HTTPException) as exc:
            raise Unavailable('Cyborg is offline, paused or busy') from exc
        except (ValueError, KeyError) as exc:
            raise InvalidOutput('Cyborg returned invalid JSON') from exc
        finally: connection.close()
    if provider != 'google': raise ValueError('Unknown Workspace provider')
    # Older configured Google models reject large open-object array grammars.
    # Limit one model response, not the total work: subsequent batches continue.
    wire_schema=copy.deepcopy(schema)
    calls=wire_schema.get('properties',{}).get('calls')
    if calls and calls.get('type')=='array':
        calls['maxItems']=min(4,calls.get('maxItems',4))
        instruction+=' Compose at most four calls in this response. Continue with another batch or individual tools for any remaining work.'
    from google.genai import types
    from lib.ai_client import get_client
    contents = [json.dumps(context, ensure_ascii=False)]
    if image: contents.append(types.Part.from_bytes(data=image_bytes(image), mime_type='image/jpeg'))
    try:
        response = get_client().models.generate_content(
            model=current_app.config['GEMINI_MODEL'], contents=contents,
            config=types.GenerateContentConfig(temperature=0, system_instruction=instruction,
                response_mime_type='application/json', response_json_schema=wire_schema, max_output_tokens=4096,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                http_options=types.HttpOptions(timeout=180000,retry_options=types.HttpRetryOptions(attempts=1))))
        return json.loads(response.text)
    except (ValueError, TypeError) as exc:
        raise InvalidOutput('Google returned invalid structured output') from exc
    except Exception as exc:
        code=getattr(exc,'code',0)
        if code==400:raise InvalidOutput('The provider rejected this structured operation format. Use individual tools or a simpler operation plan.') from exc
        response=getattr(exc,'response',None)
        delay=60
        try:delay=int(response.headers.get('Retry-After','60'))
        except (AttributeError,TypeError,ValueError):pass
        raise Unavailable('Google is unavailable',retry_after=delay,code='rate-limit' if code==429 else 'provider-configuration' if code in (400,401,403,404) else 'provider-unavailable') from exc
