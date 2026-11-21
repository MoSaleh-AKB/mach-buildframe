# fake-city

exp2.py runs the shared brain against a fake City (scripted Ledger, in-memory mailbox), no network.
Run it with the python of the installed city CLI so city_sdk imports:
  ~/.local/share/uv/tools/city-cli/bin/python -I tools/fake-city/exp2.py asst/_shared/agent.py
To test a booking flow, edit the scenario at the bottom: send a citizen need.request with a prescription_number, feed Ledger lines (prescription, agenda, slots with one slot_id= line each), then a confirm; read what the brain said.
Local `city run` cannot type multi-line Ledger worlds, so use this for offers, cancels and rebooking.
