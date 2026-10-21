/* ==========================================================================
   upload.js — File Upload (Drag & Drop + Browse)
   ========================================================================== */

const UploadManager = (() => {
  function init(root = document) {
    const zones = root.querySelectorAll('.upload-zone');
    zones.forEach((zone) => {
      if (zone.dataset.bound === 'true') return; // avoid double-binding
      zone.dataset.bound = 'true';
      const input = zone.querySelector('input[type="file"]');
      zone.addEventListener('click', () => input && input.click());

      zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
      zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
      zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
      });

      if (input) {
        input.addEventListener('change', (e) => {
          if (e.target.files.length) handleFile(e.target.files[0]);
        });
      }
    });
  }

  async function handleFile(file) {
    const validExt = /\.(csv|xlsx|xls|xlsm|tsv|txt|pdf)$/i.test(file.name);
    if (!validExt) {
      Toast.error('Unsupported file type. Supported formats: .CSV, .XLSX, .XLS, .XLSM, .TSV, .TXT, .PDF.');
      return;
    }

    document.dispatchEvent(new CustomEvent('upload:start', { detail: { filename: file.name } }));

    try {
      const provenance = document.getElementById('uploadProvenance')?.value || 'user_uploaded';
      const result = await API.uploadFile(file, provenance);
      Toast.success(result.message || `Imported ${result.rows_imported} rows as ${provenance}.`);
      const der = (result.validation_report && result.validation_report.derivations) || {};
      const fields = Object.keys(der);
      if (fields.length) {
        Toast.info(`Not in your file, derived by rule: ${fields.join(', ')}. See the batch details before using results.`);
      }
      document.dispatchEvent(new CustomEvent('upload:success', { detail: result }));
    } catch (err) {
      Toast.error(err.message || 'Upload failed. Please check your file and try again.');
      document.dispatchEvent(new CustomEvent('upload:error', { detail: { error: err.message } }));
    }
  }

  return { init, handleFile };
})();
