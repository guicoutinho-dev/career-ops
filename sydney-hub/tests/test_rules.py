from hub.domain.rules import RELEVANT_SCORE, classify


def test_stay_post_extracts_weekly_price_and_suburb():
    body = "Private room in Rosebery, $300 per week, bills included. Bond 2 weeks. Available 10 Dec"
    c = classify(body)
    assert c.kind == "stay"
    assert c.price_week == 300
    assert c.suburb == "Rosebery"
    assert c.bills_included is True


def test_bond_line_is_not_taken_as_weekly_rent():
    body = "Quarto em Kogarah.\nBond $1200\nAluguel $320 por semana"
    c = classify(body)
    assert c.kind == "stay"
    assert c.price_week == 320


def test_job_post_extracts_hourly_rate_ticket_and_phone():
    body = "Estamos procurando cleaner para obra. $36/hr, White Card obrigatório. Ligar 0491 617 364"
    c = classify(body)
    assert c.kind == "job"
    assert c.pay_hourly == 36
    assert c.ticket == "White Card"
    assert c.phone == "61491617364"


def test_job_without_ticket_is_no_ticket():
    c = classify("Hiring kitchen hand, $30 per hour, immediate start")
    assert c.kind == "job"
    assert c.ticket == "No Ticket (ABN/TFN)"


def test_rsa_ticket_detected_for_bar_work():
    c = classify("Bartender wanted, RSA required, $35/h casual")
    assert c.ticket == "RSA / RCG"


def test_unrelated_post_is_other():
    assert classify("Alguém sabe onde comprar chip Telstra?").kind == "other"


def test_empty_body_is_other():
    assert classify("").kind == "other"


def test_scam_signals_are_flagged_and_lower_score():
    clean = classify("Private room Rosebery $300 per week, inspection welcome")
    scam = classify("Private room Rosebery $300 per week. Pay deposit before inspection via gift card")
    assert scam.scam_flags
    assert not clean.scam_flags
    assert scam.score < clean.score


def test_cheap_ensuite_stay_is_relevant():
    c = classify("Private room with ensuite in Kogarah $320 per week bills included, available now")
    assert c.score >= RELEVANT_SCORE


def test_overpriced_stay_is_not_relevant():
    c = classify("Room in Bondi $900 per week")
    assert c.score < RELEVANT_SCORE


def test_car_rental_is_not_a_stay():
    assert classify("[CAR RENTAL] Precisando de um carro para alugar? Honda Civic $180 per week").kind == "other"


def test_job_seeker_is_not_a_job_offer():
    assert classify("Olá pessoal, estou procurando emprego de segunda a quarta, tenho ABN").kind == "other"
    assert classify("Hi everyone! I'm looking for casual hospitality work in Sydney, have RSA").kind == "other"


def test_room_seeker_is_not_a_stay_offer():
    assert classify("Oi, pessoal! Alguém alugando quarto ou acomodação em Sydney até $350?").kind == "other"
    assert classify("Looking for a room in Rockdale, budget $300 per week").kind == "other"


def test_suburb_is_the_first_one_mentioned():
    c = classify("QUARTO DISPONÍVEL - ARNCLIFFE, em frente à estação. 5 min de Wolli Creek. $330 por semana")
    assert c.suburb == "Arncliffe"


def test_phone_is_normalised_from_international_format():
    c = classify("Room Rockdale $350 pw, call +61 412 345 678")
    assert c.phone == "61412345678"
