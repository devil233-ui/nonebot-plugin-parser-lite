(() => {
  // 创建一个序列化函数，可以传入任何对象
  const serializeObject = (obj) => {
    // 定义浏览器内置的常见属性和方法
    const browserBuiltIns = new Set([
      // Window 对象属性
      "window",
      "document",
      "location",
      "history",
      "navigator",
      "screen",
      "frames",
      "self",
      "top",
      "parent",
      "opener",
      "frameElement",
      "external",
      "length",
      "closed",
      "name",
      "status",
      "defaultStatus",
      "toolbar",
      "menubar",
      "scrollbars",
      "resizable",
      "personalbar",
      "locationbar",
      "statusbar",
      "innerWidth",
      "innerHeight",
      "outerWidth",
      "outerHeight",
      "devicePixelRatio",
      "pageXOffset",
      "pageYOffset",
      "scrollX",
      "scrollY",

      // Window 对象方法
      "alert",
      "confirm",
      "prompt",
      "print",
      "stop",
      "focus",
      "blur",
      "close",
      "open",
      "openDialog",
      "showModalDialog",
      "clearInterval",
      "clearTimeout",
      "setInterval",
      "setTimeout",
      "requestAnimationFrame",
      "cancelAnimationFrame",
      "postMessage",
      "blur",
      "captureEvents",
      "releaseEvents",
      "getComputedStyle",
      "matchMedia",
      "moveBy",
      "moveTo",
      "resizeBy",
      "resizeTo",
      "scroll",
      "scrollBy",
      "scrollTo",
      "find",
      "getSelection",
      "removeEventListener",
      "addEventListener",
      "dispatchEvent",
      "attachEvent",
      "detachEvent",
      "scrollByLines",
      "scrollByPages",
      "sizeToContent",
      "updateCommands",

      // DOM 相关
      "Image",
      "Audio",
      "Option",
      "XMLHttpRequest",
      "WebSocket",
      "Worker",
      "SharedWorker",
      "MutationObserver",
      "IntersectionObserver",
      "ResizeObserver",
      "Promise",
      "fetch",
      "indexedDB",
      "webkitStorageInfo",
      "localStorage",
      "sessionStorage",
      "crypto",
      "cryptoKey",
      "atob",
      "btoa",
      "TextDecoder",
      "TextEncoder",
      "URL",
      "URLSearchParams",
      "AbortController",
      "Event",
      "CustomEvent",
      "KeyboardEvent",
      "MouseEvent",
      "FormData",
      "Headers",
      "Request",
      "Response",
      "BroadcastChannel",
      "MessageChannel",
      "MessagePort",
      "Notification",
      "Performance",
      "PerformanceNavigation",
      "PerformanceTiming",
      "Screen",
      "Storage",
      "FileReader",
      "Blob",
      "File",
      "DataTransfer",
      "CanvasRenderingContext2D",
      "WebGLRenderingContext",

      // 构造函数和全局对象
      "Array",
      "Object",
      "Function",
      "Number",
      "Boolean",
      "String",
      "Symbol",
      "Date",
      "RegExp",
      "Error",
      "EvalError",
      "RangeError",
      "ReferenceError",
      "SyntaxError",
      "TypeError",
      "URIError",
      "ArrayBuffer",
      "Int8Array",
      "Uint8Array",
      "Int16Array",
      "Uint16Array",
      "Int32Array",
      "Uint32Array",
      "Float32Array",
      "Float64Array",
      "Map",
      "Set",
      "WeakMap",
      "WeakSet",
      "Proxy",
      "Reflect",
      "BigInt",
      "BigInt64Array",
      "BigUint64Array",
      "Intl",
      "JSON",

      // 控制台
      "console",

      // 其他
      "isNaN",
      "isFinite",
      "parseInt",
      "parseFloat",
      "encodeURIComponent",
      "decodeURIComponent",
      "encodeURI",
      "decodeURI",
      "escape",
      "unescape",
      "eval",
      "uneval",
      "arguments",
      "undefined",
      "NaN",
      "Infinity",

      // 监听器
      "onabort",
      "onafterprint",
      "onanimationcancel",
      "onanimatinend",
      "onanimatiooniteration",
      "onanimationstart",
      "onappinstalled",
      "onauxclick",
      "onbeforeinput",
      "onbeforeinstallprompt",

      "onbeforematch",

      "onbeforeprint",

      "onbeforetoggle",

      "onbeforeunload",

      "onbeforexrselect",

      "onblur",

      "oncancel",

      "oncanplay",

      "oncanplaythrough",

      "onchange",

      "onclick",

      "onclose",
      "oncommand",
      "oncontentvisibilityautostatechange",
      "oncontextlost",
      "oncontextmenu",
      "oncontextrestored",
      "oncuechange",
      "ondblclick",
      "ondevicemotion",
      "ondeviceorientation",
      "ondeviceorientationabsolute",
      "ondrag",
      "ondragend",
      "ondragenter",
      "ondragleave",
      "ondragover",
      "ondragstart",
      "ondrop",
      "ondurationchange",
      "onemptied",
      "onended",
      "onfocus",
      "onformdata",
      "ongamepadconnected",
      "ongamepaddisconnected",
      "ongotpointercapture",
      "onhashchange",
      "oninput",
      "oninvalid",
      "onkeydown",
      "onkeypress",
      "onkeyup",
      "onlanguagechange",
      "onload",
      "onloadeddata",
      "onloadedmetadata",
      "onloadstart",
      "onlostpointercapture",
      "onmessage",
      "onmessageerror",
      "onmousedown",
      "onmouseenter",
      "onmouseleave",
      "onmousemove",
      "onmouseout",
      "onmouseover",
      "onmouseup",
      "onmousewheel",
      "onoffline",
      "ononline",
      "onpagehide",
      "onpagereveal",
      "onpageshow",
      "onpageswap",
      "onpause",
      "onplay",
      "onplaying",
      "onpointercancel",
      "onpointerdown",
      "onpointerenter",
      "onpointerleave",
      "onpointermove",
      "onpointerout",
      "onpointerover",
      "onpointerrawupdate",
      "onpointerup",
      "onpopstate",
      "onprogress",
      "onratechange",
      "onrejectionhandled",
      "onreset",
      "onresize",
      "onscroll",
      "onscrollend",
      "onscrollsnapchange",
      "onscrollsnapchanging",
      "onsearch",
      "onsecuritypolicyviolation",
      "onseeked",
      "onseeking",
      "onselect",
      "onselectionchange",
      "onselectstart",
      "onslotchange",
      "onstalled",
      "onstorage",
      "onsubmit",
      "onsuspend",
      "ontimeupdate",
      "ontoggle",
      "ontransitioncancel",
      "ontransitionend",
      "ontransitionrun",
      "ontransitionstart",
      "onunhandledrejection",
      "onunload",
      "onvolumechange",
      "onwaiting",
      "onwebkitanimationend",
      "onwebkitanimationiteration",
      "onwebkitanimationstart",
      "onwebkittransitionend",
      "onwheel",
    ]);

    const result = {};

    try {
      // 如果是 window 对象，特别处理，只包含非内置属性
      if (obj === window) {
        for (let prop in obj) {
          if (!obj.hasOwnProperty(prop) || browserBuiltIns.has(prop)) continue;

          try {
            const propValue = obj[prop];

            if (typeof propValue === "function") continue;

            if (propValue != null && typeof propValue === "object") {
              try {
                String(propValue);
                JSON.stringify(propValue);
              } catch (e) {
                // 标记为“无法安全序列化”的占位
                result[prop] = "[Property: cannot serialize safely]";
                continue;
              }
            }

            result[prop] = propValue;
          } catch (e) {
            console.log(`Skipping property ${prop}: ${e.message}`);
            result[prop] = `[PropertyAccessError: ${e.message}]`;
          }
        }
      } else {
        // 对于非 window 对象，直接复制所有可访问的属性
        for (let prop in obj) {
          if (!Object.prototype.hasOwnProperty.call(obj, prop)) continue;

          try {
            const propValue = obj[prop];

            // 跳过函数
            if (typeof propValue === "function") {
              continue;
            }

            result[prop] = propValue;
          } catch (e) {
            console.log(`Skipping property ${prop}: ${e.message}`);
            // 在结果里也写一条占位，表示这个属性存在但无法访问
            result[prop] = `[PropertyAccessError: ${e.message}]`;
          }
        }
      }
    } catch (e) {
      console.error("Error accessing object properties:", e);
    }

    // 递归安全序列化：尽量保留可序列化部分，局部失败时降级
    const safeSerialize = (value, seen = new WeakSet()) => {
      try {
        if (value === null || value === undefined) return null;

        const t = typeof value;
        if (t === "string" || t === "number" || t === "boolean") return value;

        // 避免 Date/RegExp 等直接 JSON.stringify 掉
        if (value instanceof Date) return value.toISOString();
        if (value instanceof RegExp) return String(value);

        if (t === "object") {
          // 循环引用处理
          if (seen.has(value)) {
            return "[Circular]";
          }
          seen.add(value);

          // Array
          if (Array.isArray(value)) {
            const arr = [];
            for (let i = 0; i < value.length; i++) {
              try {
                arr[i] = safeSerialize(value[i], seen);
              } catch {
                arr[i] = "[Item: cannot serialize]";
              }
            }
            return arr;
          }

          // 普通对象
          const objOut = {};
          for (const key in value) {
            if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
            try {
              objOut[key] = safeSerialize(value[key], seen);
            } catch (e) {
              objOut[key] =
                `[Property: cannot serialize: ${e && e.message ? e.message : "unknown error"}]`;
            }
          }
          return objOut;
        }

        // 其他类型一律标记
        return "[Type: unsupported]";
      } catch (e) {
        return `[SerializationError: ${e.message}]`;
      }
    };
    // 手动构建 JSON 字符串以避免安全错误
    let jsonStr = "{";
    const entries = Object.entries(result);

    for (let i = 0; i < entries.length; i++) {
      const [key, value] = entries[i];

      try {
        const safeValue = safeSerialize(value);
        const serializedValue = JSON.stringify(safeValue);

        jsonStr += `${JSON.stringify(key)}:${serializedValue}`;

        if (i < entries.length - 1) {
          jsonStr += ",";
        }
      } catch (e) {
        jsonStr += `${JSON.stringify(key)}:"[SerializationError: ${e.message}]"`;
        if (i < entries.length - 1) {
          jsonStr += ",";
        }
      }
    }

    jsonStr += "}";
    console.log(JSON.parse(jsonStr)); // 控制台会以对象形式展示，不带转义

    //console.log(jsonStr);
    //return jsonStr;
  };

  window.serializeObject = serializeObject;
})();

// 现在您可以使用这个函数来序列化任何对象
// 例如：
// window.serializeObject(window);           // 序列化 window 对象
// window.serializeObject(yourCustomObject); // 序列化自定义对象
