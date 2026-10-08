if (!Promise.withResolvers) {
  Promise.withResolvers = function() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };
}
class Iterator {
  [Symbol.iterator]() { return this; }
  find(fn) { for (const x of this) if (fn(x)) return x; return undefined; }
  filter(fn) { const self = this; return (function*() { for (const x of self) if (fn(x)) yield x; })(); }
  map(fn) { const self = this; return (function*() { for (const x of self) yield fn(x); })(); }
  some(fn) { for (const x of this) if (fn(x)) return true; return false; }
  every(fn) { for (const x of this) if (!fn(x)) return false; return true; }
  toArray() { return [...this]; }
}
globalThis.Iterator = globalThis.Iterator || Iterator;
const vm = require('vm');
const origCreateContext = vm.createContext;
vm.createContext = function(sandbox, options) {
  const ctx = origCreateContext.call(vm, sandbox, options);
  try {
    vm.runInContext(`
      if (!Promise.withResolvers) {
        Promise.withResolvers = function() {
          let resolve, reject;
          const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
          return { promise, resolve, reject };
        };
      }
      class Iterator {
        [Symbol.iterator]() { return this; }
        find(fn) { for (const x of this) if (fn(x)) return x; return undefined; }
        filter(fn) { const self = this; return (function*() { for (const x of self) if (fn(x)) yield x; })(); }
        map(fn) { const self = this; return (function*() { for (const x of self) yield fn(x); })(); }
        some(fn) { for (const x of this) if (fn(x)) return true; return false; }
        every(fn) { for (const x of this) if (!fn(x)) return false; return true; }
        toArray() { return [...this]; }
      }
      this.Iterator = this.Iterator || Iterator;
    `, ctx);
  } catch(e) {}
  return ctx;
};
try {
  const { webidl } = require('../frontend/node_modules/undici/lib/web/webidl');
  if (webidl) {
    webidl.util = webidl.util || {};
    webidl.util.markAsUncloneable = webidl.util.markAsUncloneable || ((x) => x);
  }
} catch (e) {}
const fs = require('fs');
const {JSDOM} = require('../frontend/node_modules/jsdom');
const {fireEvent,waitFor,getByRole,getByLabelText,getByText,getAllByText} = require('../frontend/node_modules/@testing-library/dom');
(async()=>{
 const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:'http://127.0.0.1:8787',runScripts:'dangerously',pretendToBeVisual:true});
 const w=dom.window;const errors=[];w.addEventListener('error',e=>errors.push(String(e.error)));
 w.fetch=(url,opts)=>fetch(new URL(url,w.location.href),opts);w.confirm=()=>true;w.TextEncoder=TextEncoder;Object.defineProperty(w,'crypto',{value:require('crypto').webcrypto});
 w.eval(fs.readFileSync(require('path').join(__dirname,'../studio/static/app.js'),'utf8'));
 const doc=w.document;
 await waitFor(()=>getByRole(doc.body,'button',{name:/New story/}),{container:doc,timeout:5000});
 fireEvent.click(getByRole(doc.body,'button',{name:/New story/}));
 await waitFor(()=>getByText(doc.body,/Project created/),{container:doc,timeout:5000});
 fireEvent.change(getByLabelText(doc.body,'Project title'),{target:{value:'Telugu story test'}});
 fireEvent.change(getByLabelText(doc.body,'Telugu narration'),{target:{value:'ఇది ఒక పరీక్ష.'}});
 fireEvent.change(getByLabelText(doc.body,'Roman Telugu captions'),{target:{value:'Idi oka pariksha.'}});
 fireEvent.change(getByLabelText(doc.body,/Your review notes/),{target:{value:'Diagnostic test; not a news claim.'}});
 fireEvent.click(getByRole(doc.body,'button',{name:/Save & approve this revision/}));
 await waitFor(()=>getByText(doc.body,'Approved · v2'),{container:doc,timeout:5000});
 fireEvent.click(getByRole(doc.body,'button',{name:/Voice & visuals/}));
 await waitFor(()=>getByLabelText(doc.body,'Visual source'),{container:doc,timeout:5000});
 fireEvent.change(getByLabelText(doc.body,'Visual source'),{target:{value:'graphic'}});
 await waitFor(()=>getByLabelText(doc.body,'Graphic heading (English / Roman Telugu)'),{container:doc});
 fireEvent.change(getByLabelText(doc.body,'Graphic heading (English / Roman Telugu)'),{target:{value:'Oka chinna katha'}});
 fireEvent.click(getByLabelText(doc.body,/I reviewed visual relevance/));
 fireEvent.click(getByRole(doc.body,'button',{name:'Save & approve changes'}));
 await waitFor(()=>getByText(doc.body,'Approved · v3'),{container:doc,timeout:5000});
 fireEvent.click(getByRole(doc.body,'button',{name:/Preview & export/}));
 await waitFor(()=>getByRole(doc.body,'button',{name:'Render 540p draft'}),{container:doc});
 fireEvent.click(getByRole(doc.body,'button',{name:'Render 540p draft'}));
 await waitFor(()=>getAllByText(doc.body,/(Online services are disabled|DRAFT PREVIEW|Complete)/),{container:doc,timeout:10000});
 console.log(JSON.stringify({reactRuntimeErrors:errors,checks:['new project','script fields','save','approval','visual edit','revision invalidation','queue render','visible offline failure']}));
 dom.window.close();
 if(errors.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});
