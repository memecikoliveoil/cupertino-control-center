# Translation helpers (need python3-babel and gettext installed).
#   make pot        regenerate po/cupertino-control-center.pot from the _() strings in the code
#   make update-po  merge the new template into every po/*.po (new strings get an empty msgstr)

DOMAIN := cupertino-control-center
POT := po/$(DOMAIN).pot

.PHONY: pot update-po

pot:
	pybabel extract -c "Translators:" --add-location=file --sort-by-file --project="Cupertino Control Center" \
		--copyright-holder="Cupertino Control Center contributors" -o $(POT) main.py cupertinocontrolcenter

update-po: pot
	for f in po/*.po; do msgmerge --update --backup=none --no-fuzzy-matching $$f $(POT); done
