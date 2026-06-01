const renderBoldText = (text) => {
  if (!text) return '';
  const parts = text.split('**');
  return parts.map((part, index) => {
    if (index % 2 === 1) {
      return `[STRONG:${part}]`;
    }
    return part;
  });
};

const sample = "🔍 I started by reading your question very closely. I've successfully interpreted your intent as a custom business **chart request** request, meaning you're looking for structured data trends.";
console.log(renderBoldText(sample).join(""));
