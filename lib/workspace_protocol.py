"""Reusable typed tools, capability discovery and composable operation inputs."""
import inspect
import copy
import json
import math
import re
import types
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

# Definitions describe effects once, instead of encoding them in every agent phase.
CAPABILITIES={
 'manage_task':('task',('create','edit','complete','reopen','cancel','restore','delete'),False),
 'create_calendar_event':('calendar',('create',),False),'edit_calendar_event':('calendar',('edit',),False),
 'delete_calendar_event':('calendar',('delete',),False),
 'manage_playlist':('playlist',('add','sync','pause','resume','schedule','detach','delete_files'),False),
 'manage_video':('video',('add','retry','delete'),False),
 'append_diary_entry':('diary',('append',),True),
 'create_folder':('file',('create',),False),'move_file':('file',('move',),False),
 'copy_file':('file',('copy',),False),'rename_path':('file',('rename',),False),
 'write_text_file':('file',('write',),True),'edit_text_file':('file',('edit',),True),
 'write_obsidian_note':('note',('write',),True),
}
META_TOOLS={'describe_tools','request_capability','compose_operations'}

class RejectedOperation(ValueError):
    """A proven precondition failure; no change was made by this attempt."""


def metadata(tool):
    name=tool if isinstance(tool,str) else tool.__name__
    extra=getattr(tool,'workspace_definition',{}) if not isinstance(tool,str) else {}
    domain,actions,retry=CAPABILITIES.get(name,('',(),False))
    return {'name':name,'domain':domain,'actions':list(actions),'effect':'write' if actions else 'read',
            'retry_safe':retry,**extra}


def register(tool,*,domain,actions=(),retry_safe=False,verify=None):
    """Integration point for another scoped tool; its factory owns account access."""
    CAPABILITIES[tool.__name__]=(domain,tuple(actions),retry_safe)
    tool.workspace_definition={'domain':domain,'actions':list(actions),'effect':'write' if actions else 'read','retry_safe':retry_safe}
    if verify is not None:tool.workspace_verify=verify
    return tool


def type_schema(hint):
    simple={str:'string',int:'integer',float:'number',bool:'boolean',type(None):'null'}
    if hint in simple:return {'type':simple[hint]}
    if hint in (Any,inspect.Parameter.empty):return {}
    origin=get_origin(hint)
    if origin is Literal:
        values=list(get_args(hint));return {**type_schema(type(values[0])),'enum':values}
    if origin in (types.UnionType,Union):return {'anyOf':[type_schema(v) for v in get_args(hint)]}
    if origin is list or hint is list:
        return {'type':'array','items':type_schema(get_args(hint)[0] if get_args(hint) else Any),'maxItems':32}
    if origin is dict or hint is dict:
        args=get_args(hint)
        if args and args[0] is not str:raise ValueError('Tool object keys must be strings')
        return {'type':'object','additionalProperties':type_schema(args[1]) if args else True,'maxProperties':32}
    raise ValueError('Unsupported tool parameter type')


def basic_schema(tool):
    if getattr(tool,'workspace_schema',None) is not None:return copy.deepcopy(tool.workspace_schema)
    hints=get_type_hints(tool);properties={};required=[]
    for name,param in inspect.signature(tool).parameters.items():
        prop=type_schema(hints.get(name,param.annotation))
        if param.default is inspect.Parameter.empty:required.append(name)
        else:prop['default']=param.default
        if name=='image_numbers':
            array=next((v for v in prop.get('anyOf',[prop]) if v.get('type')=='array'),None)
            if array is not None:array.update(items={'type':'integer','minimum':1,'maximum':8},maxItems=8,uniqueItems=True)
        properties[name]=prop
    return {'type':'object','properties':properties,'required':required,'additionalProperties':False}


def validate(value,spec,name='arguments',depth=0):
    if depth>8:raise ValueError('Tool input nesting is too deep')
    if 'anyOf' in spec:
        for alternative in spec['anyOf']:
            try:validate(value,alternative,name,depth+1);return
            except ValueError:pass
        raise ValueError('Wrong type for '+name)
    kind=spec.get('type')
    expected={'string':str,'integer':int,'number':(int,float),'boolean':bool,'null':type(None),'array':list,'object':dict}.get(kind)
    if expected and (not isinstance(value,expected) or kind in ('integer','number') and isinstance(value,bool)):raise ValueError('Wrong type for '+name)
    if 'enum' in spec and value not in spec['enum']:raise ValueError('Use an allowed value for '+name)
    if isinstance(value,str) and len(value)>16000:raise ValueError('Argument too long: '+name)
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if not math.isfinite(value):raise ValueError('Use a finite number for '+name)
        if 'minimum' in spec and value<spec['minimum'] or 'maximum' in spec and value>spec['maximum']:raise ValueError('Out of range: '+name)
    if isinstance(value,list):
        if len(value)<spec.get('minItems',0):raise ValueError('Not enough items: '+name)
        if len(value)>spec.get('maxItems',32):raise ValueError('Too many items: '+name)
        if spec.get('uniqueItems') and len({json.dumps(v,sort_keys=True) for v in value})!=len(value):raise ValueError('Duplicate selection: '+name)
        for i,item in enumerate(value):validate(item,spec.get('items',{}),name+'['+str(i)+']',depth+1)
    if isinstance(value,dict):
        if len(value)>spec.get('maxProperties',64) or any(not isinstance(k,str) or len(k)>100 for k in value):raise ValueError('Invalid object: '+name)
        props=spec.get('properties',{});missing=set(spec.get('required',[]))-value.keys()
        if missing:raise ValueError('Missing required argument: '+', '.join(sorted(missing)))
        extra=spec.get('additionalProperties',True)
        unknown=value.keys()-props.keys()
        if extra is False and unknown:raise ValueError('Unknown argument: '+', '.join(sorted(unknown)))
        for key,item in value.items():validate(item,props.get(key,extra if isinstance(extra,dict) else {}),key,depth+1)
    if kind is None and not isinstance(value,(str,int,float,bool,type(None),list,dict)):raise ValueError('Use JSON values for '+name)


def resolve_references(value,results,depth=0):
    if depth>8:raise ValueError('Operation references are too deep')
    if isinstance(value,dict) and set(value)=={'$join'}:
        parts=value['$join']
        if not isinstance(parts,list) or not 1<=len(parts)<=8:raise ValueError('Join 1–8 path components')
        resolved=[resolve_references(v,results,depth+1) for v in parts]
        if any(not isinstance(v,str) or v.startswith('/') or any(p in ('.','..') for p in v.split('/')) for v in resolved):raise ValueError('Join relative path components only')
        return '/'.join(v.strip('/') for v in resolved if v)
    if isinstance(value,dict) and set(value)=={'$ref'}:
        reference=value['$ref']
        if not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,39}(?:\.[A-Za-z0-9_]+){0,6}',reference):raise ValueError('Invalid operation reference')
        bits=reference.split('.')
        if bits[0] not in results:raise ValueError('This result is not available yet: '+bits[0])
        result=results[bits[0]]
        for bit in bits[1:]:
            if isinstance(result,dict) and bit in result:result=result[bit]
            elif isinstance(result,list) and bit.isdigit() and int(bit)<len(result):result=result[int(bit)]
            else:raise ValueError('The saved result has no field '+bit)
        return result
    if isinstance(value,dict):return {k:resolve_references(v,results,depth+1) for k,v in value.items()}
    if isinstance(value,list):return [resolve_references(v,results,depth+1) for v in value]
    return value


def operation_list(calls):
    if not isinstance(calls,list) or not 1<=len(calls)<=32:raise ValueError('Compose 1–32 operations at a time; continue with another batch for more')
    labels=set()
    for call in calls:
        if not isinstance(call,dict) or set(call)-{'tool','arguments','save_as'}:raise ValueError('Use tool, arguments and optional save_as for an operation')
        if not isinstance(call.get('tool'),str) or call['tool'] in META_TOOLS or not isinstance(call.get('arguments'),dict):raise ValueError('Compose actual tools, not another operation plan')
        label=call.get('save_as','')
        if label:
            if not isinstance(label,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,39}',label) or label in labels:raise ValueError('Use a unique short save_as label')
            labels.add(label)
    return calls
