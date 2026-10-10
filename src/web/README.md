# Overview presentation

Edit `templates/account.html.njk` and `templates/backup.html.njk` for overview content, and `protondrive.css` for
presentation. Routes, polling, RPC calls, native buttons, and their enabled conditions stay in
`../omv/workbench/component.d/omv-services-protondrive-status-form-page.yaml`.

The repository's `textFile` field refers to a template relative to this `templates/` directory. During the Debian
package build, `tools/build_workbench.py` replaces it with OMV's native `text` field. Generated manifests live only
inside the package; never copy the source manifests directly into OMV. Missing files, references outside the template
directory, and invalid template syntax fail the build.

Templates use the common HTML/Jinja/Nunjucks syntax subset. The assembler checks syntax with Jinja but does not render
values: OMV's Nunjucks renderer evaluates them on each status update. Escape dynamic text and attributes with `escape`.
Actions use OMV's native controls; scripts and Angular bindings do not execute inside this sanitized HTML.

Write normally indented markup. The assembler folds source newlines to spaces because OMV otherwise turns them into
`<br>` elements. The stylesheet gives these cards normal HTML whitespace behavior. Use paragraphs or explicit `<br>` for
layout; dynamic values inside `<pre>` keep their original line breaks. This is an HTML template convention, not a
general-purpose whitespace-preserving template loader.

`just dev::validate` checks assembly, dynamic content, escaping, and existing workbench wiring. `just app::build`
packages the generated manifests and stylesheet. Install through `just vm::install`, then hard-refresh OMV to reload its
route definitions.
