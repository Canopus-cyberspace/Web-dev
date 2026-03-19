const form = document.getElementById("mail-form");
const resultBox = document.getElementById("result");
const submitBtn = document.getElementById("submit-btn");

const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

function showResult(message, type) {
  resultBox.textContent = message;
  resultBox.className = `result show ${type}`;
}

function validateForm(data) {
  if (!data.name) {
    return "请输入发件人姓名。";
  }
  if (!data.email || !EMAIL_RE.test(data.email)) {
    return "请输入正确的发件人邮箱。";
  }
  if (!data.subject) {
    return "请输入邮件主题。";
  }
  if (!data.message) {
    return "请输入邮件正文。";
  }
  return "";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const formData = new FormData(form);
  const data = {
    name: (formData.get("name") || "").trim(),
    email: (formData.get("email") || "").trim(),
    subject: (formData.get("subject") || "").trim(),
    message: (formData.get("message") || "").trim(),
  };

  const validationError = validateForm(data);
  if (validationError) {
    showResult(validationError, "error");
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "发送中...";
  resultBox.className = "result";
  resultBox.textContent = "";

  try {
    const response = await fetch("/send", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
    });

    const payload = await response.json().catch(() => ({
      ok: false,
      message: "服务器返回了无法解析的响应。",
    }));

    if (!response.ok || !payload.ok) {
      if (payload.errors) {
        const details = Object.values(payload.errors).join(" ");
        showResult(`提交失败：${details}`, "error");
      } else {
        showResult(`提交失败：${payload.message || "未知错误"}`, "error");
      }
      return;
    }

    showResult(payload.message || "邮件发送成功。", "success");
    form.reset();
  } catch (error) {
    showResult("提交失败：无法连接服务器，请稍后重试。", "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "提交并发送邮件";
  }
});
