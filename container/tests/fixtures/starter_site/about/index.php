<?php

$siteName = 'Starter Site';
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>About <?= $siteName ?></title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <h1>About <?= $siteName ?></h1>
    <p>One folder is one URL: this file answers <code>/about/</code>.</p>
    <p><a href="/">Back to the start page</a></p>
    <?php require __DIR__ . '/../inc/footer.php'; ?>
</body>
</html>
