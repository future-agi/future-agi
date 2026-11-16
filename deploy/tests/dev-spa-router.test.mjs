import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../dev-cdn/spa-router.js',import.meta.url),'utf8');
const context=vm.createContext({});vm.runInContext(source,context);
const route=request=>context.handler({request});

test('rewrites only known app routes on GET and HEAD',()=>{
  for(const uri of ['/auth/jwt/login','/dashboard/observe','/dashboard/observe/project/insights','/shared/report']){
    for(const method of ['GET','HEAD']){
      const request={method,uri,querystring:{report:{value:'abc'},run:{value:'def'}},headers:{accept:{value:'text/html'}}};
      const result=route(request);
      assert.equal(result.uri,'/index.html');assert.equal(result,request);
      assert.deepEqual(result.querystring,{report:{value:'abc'},run:{value:'def'}});
      assert.deepEqual(result.headers,{accept:{value:'text/html'}});
    }
  }
});

test('does not mask real assets, APIs, unrelated paths or writes',()=>{
  for(const uri of ['/assets/missing.js','/dashboard/assets/missing.js','/favicon.ico','/api/capabilities/','/accounts/token/','/authentic','/dashboard-other','/unknown']){
    assert.equal(route({method:'GET',uri}).uri,uri);
  }
  for(const method of ['POST','PUT','PATCH','DELETE','OPTIONS'])assert.equal(route({method,uri:'/dashboard/observe'}).uri,'/dashboard/observe');
});
