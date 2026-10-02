"""Fixed presentation registry: source/model text cannot choose pill styles."""
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable

PALETTE = {
    "Critical Minerals": ("Critical minerals", "#e2f0d9", "#385723"),
    "other Critical Dependencies": ("Critical dependencies", "#e7edf4", "#33465e"),
    "Economic Coercion": ("Economic coercion", "#ddebf7", "#1f4e78"),
    "Export Controls and Sanctions": ("Export controls & sanctions", "#fbe2df", "#9c2924"),
    "Reshoring and Friendshoring": ("Reshoring & friendshoring", "#e7e9fb", "#383c83"),
    "Foreign Direct Investment (FDI) Screening": ("FDI screening", "#fce4d6", "#91420d"),
    "Weaponisation of Currency": ("Currency weaponisation", "#e5e1f2", "#514076"),
    "Sovereign Debt and Debt-Trap Diplomacy": ("Sovereign debt & leverage", "#f0e6dc", "#67492e"),
    "Intellectual Property (IP) Theft": ("IP theft", "#f5dfea", "#823452"),
    "Emerging Technologies": ("Emerging technology", "#ede2f7", "#65388b"),
    "Energy Independence and Transition": ("Energy & transition", "#fff0cb", "#79520b"),
    "Food and Water Security": ("Food & water security", "#e5efce", "#456019"),
    "Critical Infrastructure Protection": ("Critical infrastructure", "#dcefed", "#215e59"),
    "Economic Espionage": ("Economic espionage", "#e4e5ea", "#3b3c51"),
}


def badges(item, limit=3):
    rows = {r["topic"]: r for r in item.get("topic_classification", {}).get("topics", [])}
    confirmed = item.get("topic_confirmation", {})
    if confirmed.get("status") == "success":
        rows.update({r["topic"]: r for r in confirmed["topics"]})
    tags = list(dict.fromkeys(t for t in item.get("tags", []) if t in PALETTE))
    tags.sort(key=lambda t: (rows.get(t, {}).get("role") != "central", -rows.get(t, {}).get("material_probability", 0)))
    pills = [dict(topic=t, label=PALETTE[t][0], background=PALETTE[t][1], foreground=PALETTE[t][2]) for t in tags[:limit]]
    return pills, max(0, len(tags) - limit)


class TopicPills(Flowable):
    """Real rounded pills, wrapping to available PDF width without clipping."""
    def __init__(self, pills, hidden=0):
        super().__init__()
        self.pills = list(pills)
        if hidden:
            self.pills.append(dict(label=f"+{hidden} more", background="#eef0ec", foreground="#52616b"))
        self.spaceAfter = 10

    def wrap(self, availWidth, availHeight):
        self.placements = []
        x, row = 0, 0
        for pill in self.pills:
            width = stringWidth(pill["label"], "Helvetica-Bold", 8) + 16
            if x and x + width > availWidth:
                x, row = 0, row + 1
            self.placements.append((pill, x, row, width))
            x += width + 5
        self.width, self.height = availWidth, (row + 1) * 23 if self.pills else 0
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        for pill, x, row, width in self.placements:
            y = self.height - (row + 1) * 23 + 3
            c.setFillColor(colors.HexColor(pill["background"]))
            c.roundRect(x, y, width, 19, 9.5, fill=1, stroke=0)
            c.setFillColor(colors.HexColor(pill["foreground"]))
            c.setFont("Helvetica-Bold", 8)
            c.drawString(x + 8, y + 6, pill["label"])
        c.restoreState()
