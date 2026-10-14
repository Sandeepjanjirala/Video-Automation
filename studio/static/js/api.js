/**
 * Telugu Reel Studio - Centralized API Service (Stage M8)
 * 
 * Provides unified, secure AJAX communication with the Django backend:
 * - Session token management (x-studio-token)
 * - Automatic JSON serialization and header attachment
 * - Multipart FormData streaming for media assets
 * - Normalized error handling with actionable server detail
 */

(function($) {
  'use strict';

  let studioToken = '';

  const StudioApi = {
    init: function() {
      // Ready for any global AJAX configuration
    },

    setToken: function(tok) {
      studioToken = tok || '';
    },

    getToken: function() {
      return studioToken;
    },

    /**
     * Standard JSON API Request
     * Supports both signatures:
     * - StudioApi.request(path, method, data)
     * - StudioApi.request(path, data, method)
     */
    request: function(path, method, data) {
      // Argument normalization
      let httpMethod = 'GET';
      let payload = undefined;

      if (typeof method === 'string' && ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].includes(method.toUpperCase())) {
        httpMethod = method.toUpperCase();
        payload = data;
      } else if (method !== undefined) {
        payload = method;
        httpMethod = (typeof data === 'string') ? data.toUpperCase() : 'GET';
      }

      const headers = {};
      if (studioToken) {
        headers['x-studio-token'] = studioToken;
      }

      // Use fetch when available for seamless JSDOM and modern browser compatibility
      if (typeof window.fetch === 'function') {
        const fetchOpts = {
          method: httpMethod,
          headers: { ...headers }
        };

        let fetchUrl = '/api' + path;

        if (payload !== undefined && payload !== null) {
          if (httpMethod === 'GET') {
            const params = new URLSearchParams();
            Object.entries(payload).forEach(([k, v]) => {
              if (v !== undefined && v !== null) params.append(k, String(v));
            });
            const q = params.toString();
            if (q) fetchUrl += (fetchUrl.includes('?') ? '&' : '?') + q;
          } else {
            fetchOpts.headers['Content-Type'] = 'application/json; charset=utf-8';
            fetchOpts.body = JSON.stringify(payload);
          }
        }

        return window.fetch(fetchUrl, fetchOpts)
          .then(async (res) => {
            let bodyText = '';
            try { bodyText = await res.text(); } catch(e) {}
            let data = {};
            try { data = bodyText ? JSON.parse(bodyText) : {}; } catch(e) { data = { detail: bodyText }; }

            if (!res.ok) {
              const errMsg = data.detail || (typeof data === 'string' ? data : res.statusText || `HTTP ${res.status}`);
              const err = new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
              err.status = res.status;
              throw err;
            }
            return data;
          });
      }

      // Fallback to jQuery $.ajax
      const options = {
        url: '/api' + path,
        type: httpMethod,
        headers: headers,
        dataType: 'json',
        cache: false
      };

      if (payload !== undefined && payload !== null) {
        if (httpMethod === 'GET') {
          options.data = payload;
        } else {
          headers['Content-Type'] = 'application/json; charset=utf-8';
          options.data = JSON.stringify(payload);
        }
      }

      return new Promise((resolve, reject) => {
        $.ajax(options)
          .done(function(response) {
            resolve(response);
          })
          .fail(function(jqXHR, textStatus, errorThrown) {
            let errorMsg = 'Server communication error';
            try {
              if (jqXHR.responseJSON && jqXHR.responseJSON.detail) {
                const detail = jqXHR.responseJSON.detail;
                errorMsg = typeof detail === 'string' ? detail : JSON.stringify(detail);
              } else if (jqXHR.responseText) {
                const parsed = JSON.parse(jqXHR.responseText);
                errorMsg = parsed.detail || JSON.stringify(parsed);
              }
            } catch (e) {
              errorMsg = jqXHR.statusText || errorThrown || errorMsg;
            }
            const err = new Error(errorMsg);
            err.status = jqXHR.status;
            reject(err);
          });
      });
    },

    // Convenience alias for req(path, data, method)
    req: function(path, data, method) {
      return StudioApi.request(path, method || 'GET', data);
    },

    /**
     * Multipart Form Data Upload
     * @param {string} path - e.g. '/assets'
     * @param {FormData} formData
     * @returns {Promise<any>}
     */
    upload: function(path, formData) {
      const headers = {};
      if (studioToken) {
        headers['x-studio-token'] = studioToken;
      }

      if (typeof window.fetch === 'function') {
        return window.fetch('/api' + path, {
          method: 'POST',
          headers: headers,
          body: formData
        }).then(async (res) => {
          let bodyText = '';
          try { bodyText = await res.text(); } catch(e) {}
          let data = {};
          try { data = bodyText ? JSON.parse(bodyText) : {}; } catch(e) { data = { detail: bodyText }; }

          if (!res.ok) {
            const errMsg = data.detail || (typeof data === 'string' ? data : res.statusText || `Upload failed (HTTP ${res.status})`);
            const err = new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
            err.status = res.status;
            throw err;
          }
          return data;
        });
      }

      return new Promise((resolve, reject) => {
        $.ajax({
          url: '/api' + path,
          type: 'POST',
          headers: headers,
          data: formData,
          processData: false,
          contentType: false,
          dataType: 'json'
        })
        .done(function(response) {
          resolve(response);
        })
        .fail(function(jqXHR, textStatus, errorThrown) {
          let errorMsg = 'Upload failed';
          try {
            if (jqXHR.responseJSON && jqXHR.responseJSON.detail) {
              errorMsg = jqXHR.responseJSON.detail;
            } else if (jqXHR.responseText) {
              const parsed = JSON.parse(jqXHR.responseText);
              errorMsg = parsed.detail || JSON.stringify(parsed);
            }
          } catch (e) {
            errorMsg = jqXHR.statusText || errorThrown || errorMsg;
          }
          const err = new Error(errorMsg);
          err.status = jqXHR.status;
          reject(err);
        });
      });
    }
  };

  StudioApi.req = StudioApi.request;
  window.StudioApi = StudioApi;
})(typeof jQuery !== 'undefined' ? jQuery : null);
