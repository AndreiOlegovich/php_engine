<?php

$title = 'About the broken site';
echo '<a href="https://example.comhttps://example.com/about/">link</a>';
$page = new Page();
$page->create_head('About', 'A page whose call is written in the wrong dialect and whose line is far longer than the recommended one hundred and twenty characters.');

