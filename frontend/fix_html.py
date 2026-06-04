import re
import os

_dir = os.path.dirname(os.path.abspath(__file__))
_index = os.path.join(_dir, 'index.html')

with open(_index, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Fonts
content = content.replace(
    'family=Playfair+Display:wght@600;700&family=IBM+Plex+Sans:wght@300;400;500;600',
    'family=Cormorant+Garamond:wght@600;700&family=Inter:wght@300;400;500;600'
)
content = content.replace("font-family:'IBM Plex Sans'", "font-family:'Inter'")
content = content.replace("font-family:'Playfair Display'", "font-family:'Cormorant Garamond'")

# 2. Logo CSS and SVG
css_to_add = """
/* LOGO CSS */
#logoIcon { background: #000; border-radius: 8px; width: 38px; height: 38px; display: flex; align-items: center; justify-content: center; transition: background 0.3s; }
[data-theme="light"] #logoIcon { background: #fff; }
#logoIcon path, #logoIcon polygon:not(#logoHole) { fill: #fff; transition: fill 0.3s; }
[data-theme="light"] #logoIcon path, [data-theme="light"] #logoIcon polygon:not(#logoHole) { fill: #000; }
#logoHole { fill: #000; transition: fill 0.3s; }
[data-theme="light"] #logoHole { fill: #fff; }

@media (max-width: 768px) {
  .header{padding:0 14px;}
  .nav-links{display:none;}
  .logo-sub{display:none;}
  .hero{padding:40px 16px 32px;min-height:auto;}
  .hero h1{font-size:32px;}
  .hero p{font-size:13px;}
  .hero-stats{gap:20px;}
  .stat-num{font-size:22px;}
  .hero-cta{flex-direction:column;align-items:center;}
  .btn-primary,.btn-secondary{width:100%;max-width:300px;}
  .section{padding:40px 16px;}
  .section h2{font-size:24px;}
  .why-grid{grid-template-columns:1fr;}
  .pricing-grid{grid-template-columns:1fr;}
  .plan.featured{margin-top:16px;}
  .builder-card{flex-direction:column;align-items:flex-start;}
  .support-btns{flex-direction:column;}
  .support-btn{justify-content:center;}
  .input-area{padding:12px 12px 14px;}
  .chat-area{padding:16px 12px;}
  .message{max-width:100%;}
  .message.user .msg-bubble{margin-left:10px;}
  .modal{padding:24px 18px;}
  .hero-stats{flex-wrap:wrap;gap:16px;}
  .stat{min-width:80px;}
}
@media (max-width: 480px) {
  .hero h1{font-size:26px;}
  .auth-btn{padding:6px 12px;font-size:12px;}
  .theme-btn{width:30px;height:30px;font-size:14px;}
}
"""
content = content.replace("</style>", css_to_add + "\n</style>")

old_logo = """    <div class="logo-icon">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <rect x="2" y="3" width="20" height="3" rx="1.5" fill="#000"/>
        <rect x="10" y="6" width="4" height="10" fill="#000"/>
        <rect x="2" y="16" width="8" height="2.5" rx="1.25" fill="#000"/>
        <rect x="14" y="16" width="8" height="2.5" rx="1.25" fill="#000"/>
        <rect x="1" y="19" width="22" height="2.5" rx="1.25" fill="#000"/>
      </svg>
    </div>"""

new_logo = """    <div class="logo-icon" id="logoIcon">
      <svg id="logoSvg" width="28" height="20" viewBox="0 0 56 40" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M22 4 C10 4 2 11 2 20 C2 29 10 36 22 36 L22 29 C14 29 9 25 9 20 C9 15 14 11 22 11 Z"/>
        <polygon points="36,4 54,36 47,36 36,14 25,36 18,36"/>
        <polygon points="29,26 43,26 41,21 31,21" id="logoHole"/>
      </svg>
    </div>"""
content = content.replace(old_logo, new_logo)

# 3. Nav links
old_nav = """    <nav class="nav-links">
      <a class="nav-link" onclick="scrollTo('why')">Why AskTax</a>
      <a class="nav-link" onclick="scrollTo('pricing')">Pricing</a>
      <a class="nav-link" onclick="scrollTo('support')">Support</a>
    </nav>"""
new_nav = """    <nav class="nav-links">
      <a class="nav-link" href="#why" onclick="scrollToSection('why');return false;">Why AskTax</a>
      <a class="nav-link" href="#pricing" onclick="scrollToSection('pricing');return false;">Pricing</a>
      <a class="nav-link" href="#support" onclick="scrollToSection('support');return false;">Support</a>
    </nav>"""
content = content.replace(old_nav, new_nav)

# Fix JS scrollTo -> scrollToSection
content = content.replace("function scrollTo(id)", "function scrollToSection(id)")

# 4. Remove Lang toggle and query counter in header
content = re.sub(r'<div class="lang-toggle">.*?</div>', '', content, flags=re.DOTALL)
content = re.sub(r'<div class="query-counter" id="queryCounter">.*?</div>', '', content, flags=re.DOTALL)

# Also remove queryCounter updates in JS
content = content.replace("document.getElementById('queryCounter').classList.add('hidden');", "")
content = content.replace("document.getElementById('queryCounter').classList.remove('hidden');", "")

# 5. Hero section
old_hero = """    <!-- HERO -->
    <div class="hero">
      <div class="hero-badge">🇵🇰 Pakistan's First AI Tax Assistant</div>
      <h1>Pakistan Tax<br><span>Answered.</span></h1>
      <p>Ask any question about Pakistan's Income Tax — circulars, SROs, ordinance amendments — answered instantly with source citations from 50 years of FBR documents.</p>
      <div class="hero-urdu">پاکستان کے انکم ٹیکس کے بارے میں کوئی بھی سوال پوچھیں</div>
      <div class="hero-cta">
        <button class="btn-primary" onclick="startChat()">Ask a Question Free →</button>
        <button class="btn-secondary" onclick="scrollTo('pricing')">View Pricing</button>
      </div>
      <div class="hero-stats">
        <div class="stat"><div class="stat-num">50+</div><div class="stat-label">Years of FBR Data</div></div>
        <div class="stat"><div class="stat-num">1,500+</div><div class="stat-label">Documents</div></div>
        <div class="stat"><div class="stat-num">29K+</div><div class="stat-label">AI Vectors</div></div>
        <div class="stat"><div class="stat-num">2</div><div class="stat-label">Languages</div></div>
      </div>
    </div>"""

new_hero = """    <!-- HERO -->
    <div class="hero">
      <div class="hero-badge">Pakistan's First AI Tax Assistant</div>
      <h1>Pakistan Tax<br><span>Answered.</span></h1>
      <p>Ask any question about Pakistan's Income Tax — circulars, SROs, ordinance amendments — answered instantly with source citations from 50 years of FBR documents.</p>
      <div class="hero-cta">
        <button class="btn-primary" onclick="startChat()">Ask 20 Questions Free →</button>
        <button class="btn-secondary" onclick="scrollToSection('pricing')">View Pricing</button>
      </div>
      <div class="hero-stats">
        <div class="stat"><div class="stat-num">50+</div><div class="stat-label">Years of FBR Data</div></div>
        <div class="stat"><div class="stat-num">2026</div><div class="stat-label">Latest Updates</div></div>
        <div class="stat"><div class="stat-num">اردو</div><div class="stat-label">+ English</div></div>
      </div>
    </div>"""
content = content.replace(old_hero, new_hero)

# 6. Why section (Urdu + English icon to 🌐)
content = content.replace('<div class="why-icon">🇵🇰</div><div class="why-title">Urdu + English</div>', '<div class="why-icon">🌐</div><div class="why-title">Urdu + English</div>')

# 7. Add ChatGPT comparison box
chatgpt_box = """
      <!-- ChatGPT vs AskTax comparison -->
      <div style="margin-top:40px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;">
        <div style="padding:20px 24px;border-bottom:1px solid var(--border);font-family:'Cormorant Garamond',serif;font-size:20px;font-weight:700;color:var(--text);">AskTax.pk vs ChatGPT</div>
        <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:var(--surface2);">
                <th style="padding:12px 20px;text-align:left;color:var(--text-dim);font-weight:600;border-bottom:1px solid var(--border);">Feature</th>
                <th style="padding:12px 20px;text-align:center;color:var(--green);font-weight:600;border-bottom:1px solid var(--border);">AskTax.pk</th>
                <th style="padding:12px 20px;text-align:center;color:var(--text-dim);font-weight:600;border-bottom:1px solid var(--border);">ChatGPT</th>
              </tr>
            </thead>
            <tbody>
              <tr style="border-bottom:1px solid var(--border);"><td style="padding:12px 20px;color:var(--text-dim);">Data Source</td><td style="padding:12px 20px;text-align:center;color:var(--green);">✓ Real FBR documents</td><td style="padding:12px 20px;text-align:center;color:var(--text-muted);">✗ General internet</td></tr>
              <tr style="border-bottom:1px solid var(--border);"><td style="padding:12px 20px;color:var(--text-dim);">Pakistan Tax Laws</td><td style="padding:12px 20px;text-align:center;color:var(--green);">✓ 50 years, up to 2026</td><td style="padding:12px 20px;text-align:center;color:var(--text-muted);">✗ Outdated / incomplete</td></tr>
              <tr style="border-bottom:1px solid var(--border);"><td style="padding:12px 20px;color:var(--text-dim);">Source Citations</td><td style="padding:12px 20px;text-align:center;color:var(--green);">✓ Links to FBR PDFs</td><td style="padding:12px 20px;text-align:center;color:var(--text-muted);">✗ No sources</td></tr>
              <tr style="border-bottom:1px solid var(--border);"><td style="padding:12px 20px;color:var(--text-dim);">Hallucination Risk</td><td style="padding:12px 20px;text-align:center;color:var(--green);">✓ Says "I don't know"</td><td style="padding:12px 20px;text-align:center;color:var(--text-muted);">✗ Confidently wrong</td></tr>
              <tr style="border-bottom:1px solid var(--border);"><td style="padding:12px 20px;color:var(--text-dim);">Urdu Support</td><td style="padding:12px 20px;text-align:center;color:var(--green);">✓ Full Urdu answers</td><td style="padding:12px 20px;text-align:center;color:var(--text-muted);">~ Partial</td></tr>
              <tr><td style="padding:12px 20px;color:var(--text-dim);">Built for CA Firms</td><td style="padding:12px 20px;text-align:center;color:var(--green);">✓ Yes</td><td style="padding:12px 20px;text-align:center;color:var(--text-muted);">✗ General purpose</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>"""
content = content.replace('      </div>\n    </div>', '      </div>\n' + chatgpt_box, 1)

# 8. Pricing PKR 0
content = content.replace('<div class="plan-price">PKR 0</div>', '<div class="plan-price" style="font-family:\'IBM Plex Mono\',monospace;">PKR 0</div>')

# 9. Payment section
old_payment = """      <div class="payment-note">
        <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:6px;">💳 How to Pay</div>
        <div style="font-size:13px;color:var(--text-dim);line-height:1.6;margin-bottom:12px;">Send payment via JazzCash or EasyPaisa to <strong style="color:var(--green);">03312228870</strong> (Shaheer Salal). Then WhatsApp the screenshot to the same number and your account will be upgraded within 1 hour.</div>
        <div class="payment-methods">
          <div class="pay-chip">📱 JazzCash — 03312228870</div>
          <div class="pay-chip">💚 EasyPaisa — 03312228870</div>
          <div class="pay-chip">🏦 Bank Transfer on request</div>
        </div>
      </div>"""

new_payment = """      <div class="payment-note">
        <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:8px;">Subscription Activation</div>
        <div style="font-size:13px;color:var(--text-dim);line-height:1.7;margin-bottom:14px;">
          Send the subscription amount to <strong style="color:var(--green);">03312228870</strong> via JazzCash or EasyPaisa, then WhatsApp your payment receipt to the same number. Your account will be upgraded within 1 hour during business hours.
        </div>
        <div class="payment-methods">
          <div class="pay-chip">📱 JazzCash</div>
          <div class="pay-chip">💚 EasyPaisa</div>
          <div class="pay-chip">🏦 Bank Transfer</div>
          <div class="pay-chip" style="color:var(--green);">03312228870</div>
        </div>
      </div>"""
content = content.replace(old_payment, new_payment)

# 10. Support section
old_support_email = '<a class="support-btn" href="mailto:shaheersalal@gmail.com?subject=AskTax.pk Support&body=Hi Shaheer," target="_blank">'
new_support_email = '<a class="support-btn" href="mailto:shaheersalal@gmail.com?subject=AskTax.pk%20Support&body=Hi%20Shaheer%2C%0A%0AI%20need%20help%20with%20AskTax.pk%3A%0A%0A" target="_blank" rel="noopener">'
content = content.replace(old_support_email, new_support_email)

# Builder links
old_builder_email = '<a class="builder-link" href="mailto:shaheersalal@gmail.com">Email</a>'
new_builder_email = '<a class="builder-link" href="mailto:shaheersalal@gmail.com?subject=AskTax.pk%20Inquiry" target="_blank" rel="noopener">Email</a>'
content = content.replace(old_builder_email, new_builder_email)

content = content.replace('href="https://www.linkedin.com/in/shaheer-salal" target="_blank"', 'href="https://www.linkedin.com/in/shaheer-salal" target="_blank" rel="noopener"')
content = content.replace('href="https://wa.me/923312228870" target="_blank"', 'href="https://wa.me/923312228870" target="_blank" rel="noopener"')

# 11. Home logo onclick
content = content.replace('onclick="goHome()"', 'onclick="goHome();return false;"')
content = content.replace('<a class="logo" onclick="goHome();return false;">', '<a class="logo" href="#" onclick="goHome();return false;">')

with open(_index, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
