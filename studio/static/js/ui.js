/**
 * Telugu Reel Studio - Fold-outs and the "How to use" guide
 *
 * Less-used options sit in <details class="fold" data-fold="key"> sections that start closed.
 * Which ones the user opened is remembered (in this browser), so redrawn scene cards and a page
 * reload keep them open. Nothing is removed: every option is still one click away.
 */
(function($) {
  'use strict';

  const STORE = 'studio.openFolds';
  const GUIDE_SEEN = 'studio.guideSeen';
  let open = new Set();
  try { open = new Set(JSON.parse(localStorage.getItem(STORE) || '[]')); } catch (e) { /* storage blocked */ }

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify([...open])); } catch (e) { /* storage blocked */ }
  }

  const Fold = {
    /** Re-open the folds inside `root` that the user left open. */
    restore: function(root) {
      $(root || document).find('details.fold[data-fold]').each(function() {
        if (open.has(this.getAttribute('data-fold'))) this.open = true;
      });
    },

    /** Open (and remember) one fold, e.g. when a button elsewhere leads into it. */
    show: function(key) {
      open.add(key);
      save();
      $(`details.fold[data-fold="${key}"]`).prop('open', true);
    },

    isOpen: key => open.has(key),

    init: function() {
      // "toggle" does not bubble, so listen in the capture phase.
      document.addEventListener('toggle', function(e) {
        const el = e.target;
        if (!el || !el.matches || !el.matches('details.fold[data-fold]')) return;
        if (el.hasAttribute('data-auto-open')) {  // opened by the app, not the user: do not remember it
          el.removeAttribute('data-auto-open');
          return;
        }
        if (el.open) open.add(el.getAttribute('data-fold'));
        else open.delete(el.getAttribute('data-fold'));
        save();
      }, true);
      Fold.restore(document);
    }
  };

  const Guide = {
    open: function(section) {
      $('#guide-modal').css('display', 'flex');
      if (section) {
        const el = document.getElementById('guide-' + section);
        if (el && el.scrollIntoView) el.scrollIntoView({block: 'start'});
      }
      try { localStorage.setItem(GUIDE_SEEN, '1'); } catch (e) { /* storage blocked */ }
    },
    close: function() {
      $('#guide-modal').hide();
    },
    /** First visit in this browser: show the guide once. */
    welcome: function() {
      let seen = true;
      try { seen = !!localStorage.getItem(GUIDE_SEEN); } catch (e) { /* storage blocked: never auto-open */ }
      if (!seen) Guide.open();
    },
    init: function() {
      $(document).on('click', '.btn-open-guide', function(e) {
        e.preventDefault();
        if (window.Projects && Projects.toggleDrawer) Projects.toggleDrawer(false);
        Guide.open($(this).attr('data-guide'));
      });
      $(document).on('click', '#btn-close-guide, #btn-close-guide-bottom', Guide.close);
      $(document).on('click', '#guide-modal', function(e) {
        if (e.target === this) Guide.close();
      });
      $(document).on('keydown', function(e) {
        if (e.key === 'Escape' && $('#guide-modal').css('display') !== 'none') Guide.close();
      });
    }
  };

  window.StudioFold = Fold;
  window.StudioGuide = Guide;
})(jQuery);
