<?php

$siteName = 'Starter Site';
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title><?= $siteName ?></title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <h1><?= $siteName ?></h1>
    <p><a href="/about/">About this site</a></p>
    <?php require __DIR__ . '/inc/footer.php'; ?>
</body>
</html>
