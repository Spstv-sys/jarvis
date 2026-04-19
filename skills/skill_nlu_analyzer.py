import logging

# Тригери для запуску NLU-аналізу
TRIGGERS = [
    "проаналізуй тон", 
    "що насправді означає", 
    "прихований зміст", 
    "перевір на іронію",
    "розшифруй повідомлення"
]

def execute(cmd, jarvis):
    # Очищаємо запит
    target_text = cmd
    for t in TRIGGERS:
        target_text = target_text.replace(t, "")
    target_text = target_text.strip()

    if not target_text:
        return "Що саме мені потрібно проаналізувати? Продиктуйте текст."

    jarvis.audio.speak("Проводжу семантичний та емоційний аналіз тексту...")

    # Спеціалізований промпт для NLU
    prompt = (
        f"Ти експерт з нейролінгвістики, психології та NLU. "
        f"Проаналізуй цей текст: «{target_text}»\n\n"
        f"Видай короткий звіт у такому форматі (без розмітки, для озвучення голосом):\n"
        f"1. Справжній намір (що людина насправді хоче сказати).\n"
        f"2. Емоційний тон (чи є тут агресія, іронія, страх, сарказм).\n"
        f"3. Маніпуляції (якщо текст неоднозначний або намагається маніпулювати — вкажи на це).\n"
    )

    try:
        # Використовуємо тихий виклик ШІ (Qwen 2.5 або DeepSeek ідеально з цим впораються)
        analysis_result = jarvis.llm.generate_response(prompt, is_system_call=True)
        
        if analysis_result:
            return analysis_result
        else:
            return "Не вдалося провести аналіз через збій нейромережі."
            
    except Exception as e:
        logging.error(f"Помилка в навичці NLU: {e}")
        return "Виникла помилка під час лінгвістичного аналізу."